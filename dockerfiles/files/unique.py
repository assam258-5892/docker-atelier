#! /usr/bin/python3
# -*- coding: utf-8 -*-

"""
sortleak - A tool to sort and leak sensitive information from files.
"""

import os
import sys

pattens = set()

def readfile(filename):
    block = ""
    try:
        with open(filename, 'r') as file:
            for line in file:
                if line.startswith('=') or line.startswith('*'):
                    continue
                if line.startswith("{"):
                    block = line
                    continue
                if line.startswith("}"):
                    block = block + line
                    pattens.add(block)
                    continue
                block = block + line                
    except Exception as e:
        print(f"Error reading file {filename}: {e}")


def main(argv):
    
    for arg in argv:
        readfile(arg)

    sorted_list = sorted(list(pattens))
    for item in sorted_list:
        print(item)
    
    exit(0)

if __name__ == "__main__":
    main(sys.argv[1:])
