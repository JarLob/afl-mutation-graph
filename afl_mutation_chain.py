#!/usr/bin/env python
#
# Reconstructs an approximate AFL mutation chain based on the file names of
# seeds in a queue
#
# Author: Adrian Herrera
#

from __future__ import print_function

from argparse import ArgumentParser
import glob
import json
import os
import re
import sys

import networkx as nx
try:
    import pygraphviz
    from networkx.drawing.nx_agraph import write_dot
except ImportError:
    try:
        import pydot
        from networkx.drawing.nx_pydot import write_dot
    except ImportError:
        print('Neither pygraphviz or pydot were found')
        raise


# Regexs for extracting mutation information from seeds in the AFL queue
QUEUE_ORIG_SEED_RE = re.compile(r'id:(?P<id>\d+),orig:(?P<orig_seed>\w+)')
QUEUE_MUTATE_SEED_RE = re.compile(r'id:(?P<id>\d+),src:(?P<src>\d+),op:(?P<op>(?!havoc|splice)\w+),pos:(?P<pos>\d+)(?:,val:(?P<val_type>[\w:]+)?(?P<val>[+-]\d+))?')
QUEUE_MUTATE_SEED_HAVOC_RE = re.compile(r'id:(?P<id>\d+),src:(?P<src>\d+),op:(?P<op>havoc),rep:(?P<rep>\d+)')
QUEUE_MUTATE_SEED_SPLICE_RE = re.compile(r'id:(?P<id>\d+),src:(?P<src_1>\d+)\+(?P<src_2>\d+),op:(?P<op>splice),rep:(?P<rep>\d+)')

# Maps short stag names to full stage names
OP_MAPPING = {
    'flip1': 'bitflip 1/1',
    'flip2': 'bitflip 2/1',
    'flip4': 'bitflip 4/1',
    'flip8': 'bitflip 8/8',
    'flip16': 'bitflip 16/8',
    'flip32': 'bitflip 32/8',
    'arith8': 'arith 8/8',
    'arith16': 'arith 16/8',
    'arith32': 'arith 32/8',
    'int8': 'interest 8/8',
    'int16': 'interest 16/8',
    'int32': 'interest 32/8',
    'ext_U0': 'user extras (over)',
    'ext_UI': 'uexer extras (insert)',
    'ext_A0': 'auto extras (over)',
    'havoc': 'havoc',
    'splice': 'splice',
}


def parse_args():
    parser = ArgumentParser(description='Recover (approximate) mutation chain '
                                        'from an AFL seed')
    parser.add_argument('-f', '--output-format', default='json',
                        choices=['json', 'dot'], help='Output format')
    parser.add_argument('seed_path',
                        help='Path to the seed to recover mutation chain for')

    return parser.parse_args()


def fix_regex_dict(mutate_dict):
    # Remove None values
    mutate_dict = {k:v for k, v in mutate_dict.items() if v is not None}

    # Convert ints
    mutate_dict['id'] = int(mutate_dict['id'])

    if 'src' in mutate_dict:
        mutate_dict['src'] = int(mutate_dict['src'])
    if 'src_1' in mutate_dict:
        mutate_dict['src_1'] = int(mutate_dict['src_1'])
    if 'src_2' in mutate_dict:
        mutate_dict['src_2'] = int(mutate_dict['src_2'])
    if 'pos' in mutate_dict:
        mutate_dict['pos'] = int(mutate_dict['pos'])
    if 'rep' in mutate_dict:
        mutate_dict['rep'] = int(mutate_dict['rep'])
    if 'val' in mutate_dict:
        mutate_dict['val'] = int(mutate_dict['val'])

    # Expand op names to full stage names
    if 'op' in mutate_dict:
        mutate_dict['op'] = OP_MAPPING[mutate_dict['op']]

    return mutate_dict


def find_seed(seed_dir, seed_id):
    seed_path = os.path.join(seed_dir, 'id:%06d,*' % seed_id)
    seed_files = glob.glob(seed_path)

    if not seed_files:
        ret = None
    else:
        # Each seed should have a unique ID
        ret = seed_files[0]

    return ret


def gen_mutation_chain(seed_path):
    if seed_path is None:
        return None

    seed_dir, seed_name = os.path.split(seed_path)

    match = QUEUE_ORIG_SEED_RE.match(seed_name)
    if match:
        # We've reached the end of the chain. Append the original source to the
        # mutation chain and return
        return fix_regex_dict(match.groupdict())

    match = QUEUE_MUTATE_SEED_RE.match(seed_name)
    if match:
        # Recurse on the parent 'src' seed
        mutate_dict = fix_regex_dict(match.groupdict())
        parent_seed = find_seed(seed_dir, mutate_dict['src'])

        mutate_dict['src'] = [gen_mutation_chain(parent_seed)]

        return mutate_dict

    match = QUEUE_MUTATE_SEED_HAVOC_RE.match(seed_name)
    if match:
        # Recurse on the parent 'src' seed
        mutate_dict = fix_regex_dict(match.groupdict())
        parent_seed = find_seed(seed_dir, mutate_dict['src'])

        mutate_dict['src'] = [gen_mutation_chain(parent_seed)]

        return mutate_dict

    match = QUEUE_MUTATE_SEED_SPLICE_RE.match(seed_name)
    if match:
        # Spliced seeds have two parents. Recurse on both
        mutate_dict = fix_regex_dict(match.groupdict())
        parent_seed_1 = find_seed(seed_dir, mutate_dict.pop('src_1'))
        parent_seed_2 = find_seed(seed_dir, mutate_dict.pop('src_2'))

        mutate_dict['src'] = [gen_mutation_chain(parent_seed_1),
                              gen_mutation_chain(parent_seed_2)]

        return mutate_dict

    raise Exception('Failed to find parent seed for `%s`' % seed_name)


def create_edge_label(mutate_dict):
    label = 'op: %s' % mutate_dict['op']
    if 'pos' in mutate_dict:
        label = '%s, pos: %d' % (label, mutate_dict['pos'])
    if 'val' in mutate_dict:
        label = '%s, val: %s%d' % (label, mutate_dict.get('val_type', ''),
                                    mutate_dict['val'])
    if 'rep' in mutate_dict:
        label = '%s, rep: %d' % (label, mutate_dict['rep'])

    return label


def create_graph(mutation_chain, graph=None):
    if not graph:
        graph = nx.DiGraph()

    for src in mutation_chain['src']:
        if 'orig_seed' in src:
            graph.add_edge(src['orig_seed'], mutation_chain['id'],
                           label='"%s"' % create_edge_label(mutation_chain))
        else:
            graph.add_edge(src['id'], mutation_chain['id'],
                           label='"%s"' % create_edge_label(mutation_chain))
            create_graph(src, graph)

    return graph


def main():
    args = parse_args()

    seed_path = args.seed_path
    if not os.path.isfile(seed_path):
        raise Exception('%s is not a valid seed file' % seed_path)

    mutation_chain = gen_mutation_chain(seed_path)

    if args.output_format == 'json':
        print(json.dumps(mutation_chain))
    elif args.output_format == 'dot':
        write_dot(create_graph(mutation_chain), sys.stdout)


if __name__ == '__main__':
    main()
