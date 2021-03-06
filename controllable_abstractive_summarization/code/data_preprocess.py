# coding=utf-8
# coding: utf-8
import logging
import coloredlogs

import argparse
import os
import csv
import codecs
import io
import re
import sys
import itertools
import spacy 
import unidecode

from pathlib import Path
import subword_nmt.apply_bpe as apply_bpe
import numpy as np
import matplotlib.pyplot as plt
#import pandas as pd
import torchtext.data as torchdata
from torchtext.data import Iterator
from torch.utils.data import Dataset
# logger.info(sys.getdefaultencoding())

if sys.version_info < (3, 0):
    sys.stderr = codecs.getwriter('UTF-8')(sys.stderr)
    sys.stdout = codecs.getwriter('UTF-8')(sys.stdout)
    sys.stdin = codecs.getreader('UTF-8')(sys.stdin)
else:
    sys.stderr = codecs.getwriter('UTF-8')(sys.stderr.buffer)
    sys.stdout = codecs.getwriter('UTF-8')(sys.stdout.buffer)
    sys.stdin = codecs.getreader('UTF-8')(sys.stdin.buffer)
"""
inspired by https://github.com/deepmind/rc-data/
"""
# data available here (only stories needed)
# https://cs.nyu.edu/~kcho/DMQA/

# more data needed for anonymizing
# wget https://storage.googleapis.com/deepmind-data/20150824/data.tar.gz -O - | tar -xz --strip-components=1


# for combining all documents into one
# for f in ./cnn/stories/*.story; do (cat "${f}"; echo) >> cnn.story; done
# for f in ./dailymail/stories/*.story; do (cat "${f}"; echo) >> dailymail.story; done
# then, combine the two
# cat dailymail.story cnn.story > cnn_dailymail.story
# build bpe dict
# subword-nmt learn-bpe -s 30000 < cnn_dailymail.story > cnn_dailymail.bpe
# rm cnn.story dailymail.story cnn_dailymail.story


global max_src_in_batch, max_tgt_in_batch
logger = logging.getLogger('Training log')
coloredlogs.install(logger=logger, level='DEBUG', fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


class BPEncoder():
    def __init__(self, codes):
        self.bpe = apply_bpe.BPE(codes)

    def encode(self, story):
        if type(story) == type('abc'):
            return self.bpe.process_line(story)
        else:
            return [self.bpe.process_line(line) for line in story]

class CNNDM(Dataset):
    def __init__(self, data_path, cut_off_length=None, sources=['cnn', 'dailymail']):
        super(CNNDM).__init__()

        self.cut_off_length = cut_off_length
        self.data_path = data_path

        self.cnn_stories = os.listdir(Path(data_path, 'cnn/stories/'))
        self.dm_stories = os.listdir(Path(data_path, 'dailymail/stories'))
        
        self.stories = []
        self.sources = []
        self.max_sum_length = 0
        self.cut_stories = 0
        self.story_length = []
        self.cut_summaries = 0
        
        self.word_count = 0
        self.special_characters_count = 0
        self.special_characters_distribution = {}

        
        self.cutoff_frequency = 50
        self.unicode_detector =  re.compile('[^\x00-\x7F]+')#re.compile(r'\d+')
        self.decoder = unidecode.unidecode
        # self.character_detector = re.compile('[A-Za-z]')
        self.delete_detector = re.compile('Â|â')

        if 'cnn' in sources:
            self.stories += self.cnn_stories 
            self.sources += ['cnn' for i in range(len(self.cnn_stories))]
        if 'dailymail' in sources:
            self.stories += self.dm_stories
            self.sources += ['dailymail' for i in range(len(self.dm_stories))]
        
         

    def __len__(self):
        # assert len(self.cnn_stories) + len(self.dm_stories) == len(self.stories)
        return len(self.stories)

    def __getitem__(self, idx):

        tokenized = self.tokenize(self.stories[idx], self.sources[idx])
        content, summary, anonymized, sum_len_before = self.anonymize(tokenized, self.stories[idx], self.sources[idx])
        if sum_len_before > self.max_sum_length:
            self.max_sum_length = sum_len_before
            if self.max_sum_length > 400:
                logger.info(f'Longest summary: {self.max_sum_length} tokens.')
        no_sents, sum_len_after = self.quantize(summary)
        item = {'id': self.stories[idx].split('.')[0], 'story': content, 'length_tokens': sum_len_after, 
                        'length_sentences': no_sents, 'source': self.sources[idx], 'entities': anonymized, 
                        'summary': summary}
        return item

    def get_special_char_dict(self, no_samples):
        i = 0
        for story_path, source in zip(self.stories, self.sources):
            i += 1
            mapping_filename = Path(self.data_path, '%s/tokens/%s.txt' % (source, story_path.split('.')[0]))
            if not Path.exists(mapping_filename):
                return None

            mapping = self.loadTokenMapping(mapping_filename)
            story = open(Path(self.data_path, '%s/stories/%s' % (source, story_path)), 'rb').read()
            for (start, end) in mapping:
                token = story[start:end+1].decode()
                unc = self.unicode_detector.findall(token)#.encode().decode('utf-8'))
                if len(unc) > 0: 
                    self.special_characters_count += 1
                    if token not in self.special_characters_distribution.keys():
                        self.special_characters_distribution[token] = 1
                    else:
                        self.special_characters_distribution[token] += 1
                previous_token = token
                self.word_count += 1        
            if no_samples != 0 and i >= no_samples:
                break


    def loadTokenMapping(self, filename):
        """Loads a token mapping from the given filename.
        Args:
            filename: The filename containing the token mapping.
        Returns:
            A list of (start, end) where start and
            end (inclusive) are offsets into the content for a token. The list is
            sorted.
        """

        mapping = []

        with open(filename) as f:
            line = f.readline().strip()

            for token_mapping in line.split(';'):
                if not token_mapping:
                    continue

                start, length = token_mapping.split(',')

                mapping.append((int(start), int(start) + int(length)))

            mapping.sort(key=lambda x: x[1])  # Sort by start.

        return mapping

    def tokenize(self, path, source):
        """Tokenizes a news story.
        Args:
            story: The Story.
            source (string): The source of the tokenized news story; cnn or dailymail
        Returns:
            A TokenizedStory containing the URL and the tokens or None if no token
            mapping was found for the URL.
        """

        mapping_filename = Path(self.data_path, '%s/tokens/%s.txt' % (source, path.split('.')[0]))
        if not Path.exists(mapping_filename):
            return None

        mapping = self.loadTokenMapping(mapping_filename)
        story = open(Path(self.data_path, '%s/stories/%s' % (source, path)), 'rb').read()
        tokens = []
        
        for (start, end) in mapping:
            token = story[start:end+1].decode()
            # tokens.append(str(story[start:end+1])[2:-1])
            tokens.append(token)

        return tokens


    def loadEntityMapping(self, filename):
        """Loads an entity mapping from the given filename.
        Args:
            filename: The filename containing the entity mapping.
        Returns:
            A list of (entity_index, start, end)
            where start and end (inclusive) are token offsets for an entity. The list
            is sorted.
        """

        mapping = []

        with open(filename) as f:
            line = f.readline().strip()

            for entity_mapping in line.split(';'):
                if not entity_mapping:
                    continue

                entity_index, start, end = entity_mapping.split(',')

                mapping.append((int(entity_index), int(start), int(end)))

            mapping.sort(key=lambda x: x[2])  # Sort by start.

        return mapping

    def anonymize(self, tokenized_story, path, source):
        """Anonymizes a tokenized news story.
        Args:
            tokenized_story: A TokenizedStory.
            source (string): The source of the tokenized news story; cnn or dailymail
        Returns:
            A Story containing the URL, anonymized content and anonymized highlights or
            None if no entity mapping exists for the news story.
            """

        mapping_filename = Path(self.data_path, '%s/entities/%s.txt' % (source, path.split('.')[0]))
        if not Path.exists(mapping_filename):
            return None

        mapping = self.loadEntityMapping(mapping_filename)

        mapping_index = 0
        mapping_len = len(mapping)

        new_tokens = []
        anonymization_info = {}

        i = 0
        while i < len(tokenized_story):
            if mapping_index < mapping_len and mapping[mapping_index][1] == i:
                entity_index, start, end = mapping[mapping_index]
                anonymized_entity_name = '@entity%d' % entity_index
                new_tokens.append(anonymized_entity_name)
                anonymization_info[anonymized_entity_name] = ' '.join(
                    tokenized_story[start: end + 1]).replace(' - ', '-')

                mapping_index += 1
                i = end + 1
            else:
                if tokenized_story[i] in self.special_characters_distribution.keys():
                    token = ''
                    if self.special_characters_distribution[tokenized_story[i]] > self.cutoff_frequency:
                        if len(self.delete_detector.findall(tokenized_story[i])) == 0:
                            token = self.decoder(tokenized_story[i])
                        else:
                            token = self.decoder(re.sub(self.delete_detector, '', tokenized_story[i]))
                else:
                    token = tokenized_story[i]
                if len(token) > 0:
                    new_tokens.append(token)
                i += 1
        new_tokens = ' '.join(new_tokens).split('@ highlight')
        
        content = new_tokens[0].replace('  ', ' ')
        len_content = len(content.split(' '))
        self.story_length.append(len_content)
        if len_content > self.cut_off_length:
            content = ' '.join(content.split(' ')[0:self.cut_off_length])
            self.cut_stories += 1

        summary = ' . '.join(new_tokens[1:]).replace('  ', ' ')
        len_summary = len(summary.split(' '))
        if len_summary > self.cut_off_length:
            summary = ' '.join(summary.split(' ')[0:self.cut_off_length])
            self.cut_summaries += 1

        return content, summary, anonymization_info, len_summary


    def quantize(self, summary):
            
        sents = len(summary.split('.'))
        tokens = len(summary.split(' '))

        return sents, tokens


def log_length_statements(lengths_list, label):
    logger.info(f'Maximum {label} length is {max(lengths_list)}.')
    logger.info(f'Mean {label} length is {sum(lengths_list)/len(lengths_list)}.')    
    logger.info(f'Minimum {label} length is {min(lengths_list)}.')



def anonymize_and_bpe_data(data_path=Path(Path.cwd(), 'data/'), sources=['cnn', 'dailymail'], no_samples=0, cut_off_length=None):
    
    logger.info(f'Loading data from {sources} and BPE codes...')

    dataset = CNNDM(data_path, cut_off_length, sources)
    with open(Path(data_path, 'cnn_dailymail.bpe'), 'r') as codes:
        bpencoder = BPEncoder(codes)
    
    logger.info('...done.')

    processed_data = {'id': [], 'story':[], 'length_tokens': [], 
                        'length_sentences': [], 'source': [], 'entities': [], 'summary': []}
    
    tmp_name = Path(data_path, 'tmp.csv')
    csv_name = Path(data_path, '_'.join(sources) + '.csv')
    lengths = []
    post_bpe_lengths = {'story': [], 'summary': []}

    with open(tmp_name, 'w') as tmp_file:
    
        logger.info(f'Writing to {tmp_name}, and byte pair encoding...')
        writer = csv.DictWriter(tmp_file, fieldnames=processed_data.keys())
        dataset.get_special_char_dict(no_samples)
        logger.info(f'{dataset.special_characters_count} unicode special characters words out of {dataset.word_count} words.')
        logger.info(f'{len(dataset.special_characters_distribution.keys())} unique unicode words, with {sum([count[1] > dataset.cutoff_frequency for count in dataset.special_characters_distribution.items()])} with more than {dataset.cutoff_frequency} occurences.')
        logger.info(sorted(dataset.special_characters_distribution.items(), key=lambda x: x[1], reverse=True)[0:100])

        for no, sample in enumerate(dataset):
            
            def repl(match):
                replaced = match.group(0).replace('@@ ', '')
                return replaced

            pattern = '@{3} enti@{2} ty@{2} (\d+@@ )*\d+(?!@)'
            original_summary = sample['summary']
            sample['story'] = re.sub(pattern, repl, bpencoder.encode(sample['story']))
            post_bpe_lengths['story'].append(len(sample['story'].split(' ')))
            if post_bpe_lengths['story'][-1] > 1000:
                # 1 story that contains the number Pi here; clean it
                continue
            
            
            sample['summary'] = re.sub(pattern, repl, bpencoder.encode(sample['summary']))
            post_bpe_lengths['summary'].append(len(sample['summary'].split(' ')))
            lengths.append(sample['length_tokens'])


            writer.writerow(sample)
            if no % 5000 == 0 and no != 0:
                logger.info(f'Progress: {no}/{len(dataset)} processed.')
            if no_samples is not 0 and no % no_samples == 0 and no != 0:
                break

    logger.info(sample['story'])
    logger.info(sample['summary'])
    logger.info(original_summary)
    logger.info('')
    logger.info(f'There are {dataset.cut_stories} out of {no+1} stories over the cutoff length.')
    logger.info(f'There are {dataset.cut_summaries} out of {no+1} summaries over the cutoff length.')
    bins = equal_bin(np.asarray(lengths), 10)
    len_hist = np.histogram(lengths, 10)
    logger.info('')
    logger.info(f'Maximum summary length is {dataset.max_sum_length}.')
    log_length_statements(lengths, 'summary')
    log_length_statements(post_bpe_lengths['summary'], 'summary after bpe')
    log_length_statements(dataset.story_length, 'story')
    log_length_statements(post_bpe_lengths['story'], 'story after bpe')
    
    with open(tmp_name, 'r') as tmp_file, open(csv_name, 'w') as csv_file:
        logger.info(f'Modifying lengths and writing to {csv_name}...')
        reader = csv.DictReader(tmp_file, fieldnames=processed_data.keys())
        writer = csv.DictWriter(csv_file, fieldnames=processed_data.keys())
        # for no, row in enumerate(reader):
            # lengths.append(int(row['length_tokens']))
        bins = equal_bin(np.asarray(lengths), 10)
        len_hist = np.histogram(lengths, 10)
        for no, row in enumerate(reader):
            row['length_tokens'] = bins[no] + 1
            writer.writerow(row)

    logger.info(f'Removing {tmp_name}...')    
    Path.unlink(tmp_name)
    logger.info('...done.')
    
    unique, counts = np.unique(bins, return_counts=True)
    logger.info('Confirming that bins are equal size: ')
    logger.info(np.asarray((unique, counts)).T)

    
    len_mean = sum(lengths)/len(lengths)
    min_ylim, max_ylim = plt.ylim()
    plt.hist(x=lengths, bins=10, edgecolor='k', alpha=0.7)
    plt.axvline(len_mean, color='k', linestyle='dashed', linewidth=1)
    plt.text(len_mean*1.1, max_ylim*0.9, 'Mean: {:.2f}'.format(len_mean))
    plt.xlabel('length in tokens')
    plt.ylabel('count in tokens')
    plt.savefig('summary_length_hist.png')

    # logger.info(f'FAILED ENTITIES: {failed_entities}')

def equal_bin(N, m):
    sep = (N.size/float(m))*np.arange(1,m+1)
    idx = sep.searchsorted(np.arange(N.size))
    return idx[N.argsort().argsort()]
    

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cnn', action='store_true',
                        help='Use cnn data')
    parser.add_argument('--dailymail', action='store_true',
                        help='Use dailymail data')
    parser.add_argument('--no_samples', type=int, default=0,
                        help='number of samples')

    args = parser.parse_args()    
    sources = []
    if args.cnn: sources.append('cnn') 
    if args.dailymail: sources.append('dailymail') 
    anonymize_and_bpe_data(sources=sources, no_samples=args.no_samples, cut_off_length=400)




# def batch_size_fn(new, count, sofar):
#     "Keep augmenting batch and calculate total number of tokens + padding."
#     global max_src_in_batch, max_tgt_in_batch
#     if count == 1:
#         max_src_in_batch = 0
#         max_tgt_in_batch = 0
#     max_src_in_batch = max(max_src_in_batch,  len(new.stories))
#     max_tgt_in_batch = max(max_tgt_in_batch,  len(new.summary) + 2)
#     src_elements = count * max_src_in_batch
#     tgt_elements = count * max_tgt_in_batch
#     return max(src_elements, tgt_elements)

# class MyIterator(Iterator):
#     # from https://towardsdatascience.com/how-to-use-torchtext-for-neural-machine-translation-plus-hack-to-make-it-5x-faster-77f3884d95
#     def create_batches(self):
#         if self.train:
#             def pool(d, random_shuffler):
#                 for p in torchdata.batch(d, self.batch_size * 100):
#                     p_batch = torchdata.batch(
#                         sorted(p, key=self.sort_key),
#                         self.batch_size, self.batch_size_fn)
#                     for b in random_shuffler(list(p_batch)):
#                         yield b
#             self.batches = pool(self.data(), self.random_shuffler)
            
#         else:
#             self.batches = []
#             for b in torchdata.batch(self.data(), self.batch_size,
#                                           self.batch_size_fn):
#                 self.batches.append(sorted(b, key=self.sort_key))

            # entities_in_story = re.findall('@entity[\d+ ]+', sample['stories'])
            # entities_in_summary = set(re.findall('@entity[\d+ ]+', sample['summary']))
            # entities_in_story = set(re.findall('@entity\d+', sample['stories']))
            # deviations = sum([len(re.findall('\s', ent_sample)) > 1 for ent_sample in entities_in_story]) + sum([len(re.findall('\s', ent_sample)) > 1 for ent_sample in entities_in_summary])
            # deviations = 1 if entities_in_story!=set(sample['entities'].keys())  else 0 
            # failed_entities += deviations


    # nlp = spacy.load("en", disable=["tagger", "parser", "ner"])

    # txt_field = Field(tokenize=lambda x: [tok.text for tok in nlp.tokenizer(x)], init_token='<sos>', eos_token='<eos>', lower=True, batch_first=True)
    # # txt_field = Field(tokenize=lambda x: x.split(), init_token='<sos>', eos_token='<eos>', lower=True, batch_first=True)
    # num_field = Field(sequential=False, use_vocab=False)
    # txt_nonseq_field = Field(sequential=False, use_vocab=True)
    # train_fields = [('id', None), ('stories', txt_field), ('length_tokens', num_field),
    #                 ('length_sentences', num_field), ('source', txt_nonseq_field), 
    #                 ('entities', None), ('summary', txt_field)]
    
    # logger.info('Started loading data...')

    # start = time.time()
    # dataset = TabularDataset(path='./cnn.csv', format='csv', skip_header=True, fields=train_fields)
    # end = time.time()
    
    # logger.info(f'finished in {end-start} seconds.')
    
    # logger.info('Started building vocabs...')
    
    # start = time.time()
    # txt_field.build_vocab(dataset)
    # txt_nonseq_field.build_vocab(dataset)
    # end = time.time()
    
    # logger.info(f'finished in {end-start} seconds.')

    # # train_iter = BucketIterator(dataset, batch_size=32, sort_key=lambda x: len(x.stories), shuffle=True)
    # # train_iter3 = BucketIterator(dataset, batch_size=16, sort_key=lambda x: len(x.stories), shuffle=True)
    # # train_iter4 = BucketIterator(dataset, batch_size=64, sort_key=lambda x: len(x.stories), shuffle=True)
    # train_iter = MyIterator(dataset, batch_size=20000, device=0, repeat=False, 
    #                     sort_key= lambda x:(len(x.stories), len(x.summary)),
    #                     batch_size_fn=batch_size_fn, train=True, shuffle=True)

    # # logger.info([txt_field.vocab.itos[ind] for ind in batch.stories[0]])
    
    # # logger.info(batch.length_tokens)
    # # logger.info([txt_field.vocab.itos[ind] for ind in batch.summary[0]])
    # pads = 0
    # symbs = 0

    # for no, batch in enumerate(train_iter):
    #     logger.info(f'stories in batch: {len(batch.stories)}')
    #     logger.info(f'story length: {len(batch.stories[0])}')
    #     logger.info(f'story length: {len(batch.stories[1])}')        
    #     if no == 20: 
    #         break
    #     for story in batch.stories:

    #         for ind in story:
    #             if ind == 1:
    #                 pads += 1
    #                 symbs += 1
    #             else:                
    #                 symbs += 1
            
    # logger.info(f'{symbs} tokens, out of which {100*pads/(pads+symbs)} are pads.')
    # pads = 0
    # symbs = 0

    # for no, batch in enumerate(train_iter2):
    #     # logger.info([txt_field.vocab.itos[ind] for ind in batch.stories[0]])
    #     logger.info(f'stories in batch: {len(batch.stories)}')
    #     logger.info(f'story length: {len(batch.stories[0])}')
    #     logger.info(f'story length: {len(batch.stories[1])}')
    #     if no == 20: 
    #         break
    #     for story in batch.stories:


    #         for ind in story:
    #             if ind == 1:
    #                 pads += 1
    #                 symbs += 1
    #             else:                
    #                 symbs += 1
            
    # logger.info(f'{symbs} tokens, out of which {100*pads/(pads+symbs)} are pads.')
    # # pads = 0
    # # symbs = 0

    # # for no, batch in enumerate(train_iter3):
    # #     if no == 20: 
    # #         break
    # #     for story in batch.stories:

    # #         for ind in story:
    # #             if ind == 1:
    # #                 pads += 1
    # #                 symbs += 1
    # #             else:                
    # #                 symbs += 1
            
    # # logger.info(f'{symbs} tokens, out of which {100*pads/(pads+symbs)} are pads.')
    # # pads = 0
    # # symbs = 0

    # # for no, batch in enumerate(train_iter4):
    # #     if no == 20: 
    # #         break
    # #     for story in batch.stories:

    # #         for ind in story:
    # #             if ind == 1:
    # #                 pads += 1
    # #                 symbs += 1
    # #             else:                
    # #                 symbs += 1
            
    # # logger.info(f'{symbs} tokens, out of which {100*pads/(pads+symbs)} are pads.')
    
    # # data_path = os.getcwd()
    # # dataset = CNNDM(data_path, src_field)
    # # train_iter = BucketIterator(dataset=dataset, batch_size=32, sort_key=lambda x: x.no_sents, shuffle=True)
    # # logger.info('data initialized') 
    # # logger.info(next(iter(train_iter)))
    # # for no, i in enumerate(train_iter):
    # #     logger.info('started iterating')
    # #     idx, source, content, summary, anonymized, no_tokens, no_sents = batch 
    # #     logger.info(summary)
    # #     if no == 5:
    #         break


        # while not article_processed:
        #     story = open(os.path.join(self.data_path, '%s/stories/%s' % (source, path)), 'r').read()
        #     story = story.replace('?', '~').encode('ascii', 'replace').decode('utf-8') #encode 'replace' inserts ? instead of special characters 
        #     while '?' in story:
        #         index = story.index('?')
        #         try:
        #             index_end = dict(mapping)[index]
        #         except KeyError:
        #             for (start, end) in mapping:
        #                 if index >= start and index <= end:
        #                     index_end = end
        #                 else:
        #                     index_end = index
        #         char_len = index_end + 1 - index
        #         story = story[0:index] + ''.join(['/' for i in range(char_len)]) + story[index_end+offset:len(story)+1]


        #     story = story.replace('~', '?')
        #     tokens = []
        #     for (start, end) in mapping:
        #         tokens.append(story[start:end+1])
        #     if '@' in tokens and 'highlight' in tokens:
        #         article_processed = True
        #     else:
        #         story = open(os.path.join(self.data_path, '%s/stories/%s' % (source, path)), 'r').read()
        #         tokens = self.tokenizer(story)
        #         return story, tokens, True

                



        # while not article_processed:
            
        #     encode_placeholder = ''.join(['/' for i in range(length)])
        #     story = open(os.path.join(self.data_path, '%s/stories/%s' % (source, path)), 'r').read()
        #     story_qs = story.replace('?', '&&&').encode('ascii', 'replace').decode('utf-8')
        #     story = story_qs.replace('?', encode_placeholder).replace('&&&', '?')
        #     tokens = []

        #     for (start, end) in mapping:
        #         tokens.append(story[start:end+1])

        #     if '@' in tokens and 'highlight' in tokens:
        #         article_processed = True
        #     elif length < 10:
        #         length += 1
        #     else:
        #         article_processed = True
