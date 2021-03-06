import spacy
import yaml
import io
import random
import argparse
from tqdm.auto import tqdm
from functools import partial

import torch
import torch.nn as nn
import torch.optim as optim

from torchtext.datasets import Multi30k
from torchtext.data import Field, Example, Dataset, BucketIterator, interleave_keys

from seq2seq import Seq2seq
from transformer import Transformer

def tokenize(text, spacy):
    return [tok.text for tok in spacy.tokenizer(text)]

def make_datasets(data_path, src_lang, tgt_lang, src_first, batch_size, device):

    src_field = Field(tokenize=partial(tokenize, spacy=spacy.load(src_lang)),
                init_token='<sos>',
                eos_token='<eos>',
                lower=True)

    tgt_field = Field(tokenize=partial(tokenize, spacy=spacy.load(tgt_lang)),
                init_token='<sos>',
                eos_token='<eos>',
                lower=True)

    if data_path.lower() == 'multi30k':

        exts = ('.'+src_lang, '.'+tgt_lang)
        train_data, valid_data, _ = Multi30k.splits(exts=exts, fields=(src_field, tgt_field))
    
    else:

        examples = []
        fields = [('src', src_field), ('trg', tgt_field)]
        with io.open(data_path, mode='r', encoding='utf-8') as pair_file:
            for pair_line in pair_file:
                pair_line = pair_line.strip().split('\t')
                src_line, tgt_line = pair_line[0], pair_line[1] if src_first else pair_line[1], pair_line[0]
                if src_line != '' and tgt_line != '':
                    examples.append(Example.fromlist([src_line, tgt_line], fields))
            
            # split train / valid set
            random.shuffle(examples)
            idx = int(len(examples)*0.8)
            train_example, valid_example = examples[:idx], examples[idx:]

            train_data = Dataset(train_example, fields)
            valid_data = Dataset(valid_example, fields)

    src_field.build_vocab(train_data)
    tgt_field.build_vocab(train_data)

    train_iterator, valid_iterator = BucketIterator.splits((train_data, valid_data), batch_size=batch_size, sort_key=lambda x: interleave_keys(len(x.src), len(x.trg)), device=device)
    return train_iterator, valid_iterator, src_field, tgt_field

class Trainer:

    def __init__(self, model, train_loader, val_loader, ignore_index):

        self.model = model
        self.data_loaders = {'train':train_loader, 'val':val_loader}
        self.ignore_index = ignore_index

    def train(self, epochs, learning_rate):

        optimizer = optim.Adam(self.model.parameters(), lr=learning_rate)
        criterion = torch.nn.NLLLoss(ignore_index=self.ignore_index)
        epoch_loss = {'train':0, 'val':0}

        for epoch in range(epochs):
            print (f'Epoch {epoch+1}/{epochs}')

            for phase in ['train', 'val']:
                if phase == 'train':
                    self.model.train()
                else:
                    self.model.eval()

                epoch_loss[phase] = 0
                for batch in tqdm(self.data_loaders[phase]):
                    src = batch.src
                    trg = batch.trg

                    optimizer.zero_grad()

                    loss = 0
                    with torch.set_grad_enabled(phase == 'train'):
                        output = self.model(src, trg)

                        d_out = output.shape[-1]
                        output = output.contiguous().view(-1, d_out)
                        trg = trg[1:].contiguous().view(-1)

                        loss += criterion(output, trg)
                        
                        if phase == 'train':
                            loss.backward()
                            optimizer.step()

                        epoch_loss[phase] += loss.item()

                epoch_loss[phase] /= len(self.data_loaders[phase])

            for phase in ['train', 'val']:
                print(f'{phase} Loss: {epoch_loss[phase]:.4f}')
            print('-'*10)

    def save(self, model_path, src_lang, tgt_lang, src_field, tgt_field):

        name = type(self.model).__name__
        if name == 'Seq2seq':
            params = {
                'name': self.model.name,
                'attn_name': self.model.attn_name,
                'tgt_sos': self.model.tgt_sos,
                'input_dim': self.model.input_dim,
                'output_dim': self.model.output_dim,
                'embed_dim': self.model.embed_dim,
                'hidden_dim': self.model.hidden_dim,
                'attn_dim': self.model.attn_dim,
                'num_layers': self.model.num_layers
            }
        elif name == 'Transformer':
            params = {
                'name': name,
                'src_pad': self.model.src_pad,
                'tgt_pad': self.model.tgt_pad,
                'max_len': self.model.max_len,
                'input_dim': self.model.input_dim,
                'output_dim': self.model.output_dim,
                'model_dim': self.model.model_dim,
                'ff_dim': self.model.ff_dim,
                'num_layers': self.model.num_layers,
                'num_heads': self.model.num_heads,
                'drop_prob': self.model.drop_prob
            }
        else:
            return

        state = {
            'state_dict': self.model.state_dict(),
            'parameter': params,
            'src_lang': src_lang,
            'tgt_lang': tgt_lang,
            'src_vocab': src_field.vocab,
            'tgt_vocab': tgt_field.vocab
        }
        torch.save(state, model_path)

def main(data_path, save_path, src_lang, tgt_lang, src_first,
        epochs, batch_size, learning_rate, model_params):

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    
    train_iterator, val_iterator, src_field, tgt_field = make_datasets(data_path, src_lang, tgt_lang, src_first, batch_size, device)
    
    tgt_sos = tgt_field.vocab.stoi[tgt_field.init_token]
    src_pad = src_field.vocab.stoi[src_field.pad_token]
    tgt_pad = tgt_field.vocab.stoi[tgt_field.pad_token]

    input_dim = len(src_field.vocab)
    output_dim = len(tgt_field.vocab)
    model_name = model_params['model_name']

    if model_name == 'Transformer':

        max_len = model_params['max_len']
        model_dim = model_params['model_dim']
        ff_dim = model_params['ff_dim']
        num_layers = model_params['num_layers']
        num_heads = model_params['num_heads']
        drop_prob = model_params['drop_prob']
        model = Transformer(src_pad, tgt_pad, max_len, input_dim, output_dim, model_dim, ff_dim, num_layers, num_heads, drop_prob)

    else:
        attn_name = model_params['attn_name']
        embed_dim = model_params['embed_dim']
        hidden_dim = model_params['hidden_dim']
        attn_dim = model_params['attn_dim']
        num_layers = model_params['num_layers']
        model = Seq2seq(model_name, attn_name, tgt_sos, input_dim, output_dim, embed_dim, hidden_dim, attn_dim, num_layers)
    
    model.to(device)
    trainer = Trainer(model, train_iterator, val_iterator, tgt_pad)
    trainer.train(epochs, learning_rate)
    trainer.save(save_path, src_lang, tgt_lang, src_field, tgt_field)
    
if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Train a Translator')

    parser.add_argument('src', type=str, help='Source language')
    parser.add_argument('tgt', type=str, help='Target language')
    parser.add_argument('--data-dir', type=str)
    parser.add_argument('--save-dir', type=str)
    parser.add_argument('--epochs', type=int, default=10)
    parser.add_argument('--batch', type=int, default=128)
    parser.add_argument('--lr', type=float, default=0.001)
    parser.add_argument('--src-first', action='store_true')
    parser.add_argument('--config', type=str, default='configs/train_config.yaml',
                    help='Configuration file path')

    args = parser.parse_args()
    model_params = yaml.load(open(args.config, 'r'), Loader=yaml.FullLoader)
    main(args.data_dir, args.save_dir, args.src, args.tgt, args.src_first, args.epochs, args.batch, args.lr, model_params)