import re
import io
import time
import math
import random
import argparse
import unicodedata

import torch
import torch.nn as nn
import torch.optim as optim

from tokenizer import Tokenizer
from seq2seq import Seq2seq

def load_data(data_path, input_name, target_name, encoding='utf-8'):

    input_lang = Tokenizer(input_name)
    target_lang = Tokenizer(target_name)

    # load data
    with io.open(f'{data_path}/{input_name}', encoding=encoding) as f:
        input_data = f.read().strip().split('\n')

    with io.open(f'{data_path}/{target_name}', encoding=encoding) as f:
        target_data = f.read().strip().split('\n')

    input_data = [preprocess(s) for s in input_data]
    target_data = [preprocess(s) for s in target_data]

    input_data = input_lang.to_token(input_data)
    target_data = target_lang.to_token(target_data)

    return input_lang, target_lang, list(zip(input_data, target_data))

def preprocess(s):
    # Turn a Unicode string to plain ASCII, thanks to
    # http://stackoverflow.com/a/518232/2809427
    def unicodeToAscii(s):
        return ''.join(
            c for c in unicodedata.normalize('NFD', s)
            if unicodedata.category(c) != 'Mn'
        )
    # Lowercase, trim, and remove non-letter characters 
    s = unicodeToAscii(s.lower().strip())
    s = re.sub(r'([.!?])', r' \1', s)
    s = re.sub(r'[^a-zA-Z.!?]+', r' ', s)
    return s

def pad_sequence(x):
    
    length = max([len(seq) for seq in x])
    pad_x = []
    for seq in x:
        pad_seq = seq
        if len(seq) < length:
            pad_length = length - len(seq)
            pad_seq = pad_seq + ([PAD_TOKEN]*pad_length)
        pad_x.append(pad_seq)
    return pad_x

def generate_batch(pairs, batch_size, device, shuffle=True):
    
    if shuffle:
        random.shuffle(pairs)

    steps_per_epoch = math.ceil(len(pairs)/batch_size)
    for idx in range(1, steps_per_epoch+1):
        x = []
        y = []
        for pair in pairs[(idx-1)*batch_size:idx*batch_size]:
            x.append(pair[0])
            y.append(pair[1])
            
        # sort by length (descending)
        sorted_pairs = sorted(zip(x, y), key=lambda p: len(p[0]), reverse=True)
        sorted_x, sorted_y = zip(*sorted_pairs)
        # pad
        padded_x = pad_sequence(sorted_x)
        padded_y = pad_sequence(sorted_y)
        # array to tensor
        input_tensor = torch.tensor(padded_x, dtype=torch.long, device=device)
        target_tensor = torch.tensor(padded_y, dtype=torch.long, device=device)
        yield input_tensor, target_tensor, idx, steps_per_epoch

def train(input_tensor, target_tensor, model, optimizer, criterion):

    target_length = target_tensor.size(1)

    optimizer.zero_grad()
    outputs, _ = model(input_tensor, target_length)

    loss = 0
    for i in range(target_length):
        loss += criterion(outputs[i], target_tensor[:,i])
    loss.backward()
    optimizer.step()

    return loss.item() / target_length

def as_minute(s):
    m = math.floor(s / 60)
    s -= m * 60
    return f'{m:2}m {int(s):2}s'

def print_log(start, now, loss, cur_step, total_step):

    s = now - start
    es = s / (cur_step/total_step)
    rs = es - s

    print(f'\r{as_minute(s)} (-{as_minute(rs)}({cur_step}/{total_step}) {loss:.4f}', end='')

def train_loop(pairs, model, epochs, batch_size, learning_rate, device, print_every=5.0):

    optimizer = optim.SGD(model.parameters(), lr=learning_rate)
    criterion = torch.nn.NLLLoss(ignore_index=PAD_TOKEN)

    for epoch in range(1, epochs+1):

        print (f'epoch: {epoch}/{epochs}')

        batch_loss_total = 0
        start = time.time()
        last = start
        for input_tensor, target_tensor, idx, steps_per_epoch in generate_batch(pairs, batch_size, device):
            loss = train(input_tensor, target_tensor, model, optimizer, criterion)
            batch_loss_total += loss

            now = time.time()
            if (now - last) > print_every or idx == steps_per_epoch:
                last = now
                batch_loss_avg = batch_loss_total / idx
                print_log(start, now, batch_loss_avg, idx, steps_per_epoch)
        print()

def main(path, source, target, save_path, attn_name, epochs, 
        batch_size, embed_size, hidden_size, attn_size, learning_rate):

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    
    input_lang, target_lang, pairs = load_data(path, source, target)

    model = Seq2seq(input_lang.num_words, target_lang.num_words, embed_size, hidden_size, attn_size, attn_name, device)
    train_loop(pairs, model, epochs, batch_size, learning_rate, device)

    model.save(f'{save_path}/model.pth')
    input_lang.save(f'{save_path}/{source}.pkl')
    target_lang.save(f'{save_path}/{target}.pkl')
    
if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Train a Translator')

    parser.add_argument('path', type=str,
                    help='Path of input data directory')
    parser.add_argument('input', type=str,
                    help='Input language name')
    parser.add_argument('target', type=str,
                    help='Target language name')
    parser.add_argument('--save', type=str, default='model.pkl',
                    help='Save path directory')
    parser.add_argument('--type', type=str, default='concat', ## [additive, dot, general, concat]
                    help='Attention score function')
    parser.add_argument('--epoch', type=int, default=20,
                    help='Number of epochs')
    parser.add_argument('--batch', type=int, default=64,
                    help='Number of batch sizes')
    parser.add_argument('--embed', type=int, default=256,
                    help='Number of embedding dim both encoder and decoder')
    parser.add_argument('--hidden', type=int, default=512,
                    help='Number of features of hidden layer')
    parser.add_argument('--attn', type=int, default=10,
                    help='Number of features of attention')
    parser.add_argument('--lr', type=float, default=0.001,
                    help='Learning rate')

    args = parser.parse_args()

    main(args.path, args.input, args.target, args.save, args.type, args.epoch, args.batch, args.embed, args.hidden, args.attn, args.lr)