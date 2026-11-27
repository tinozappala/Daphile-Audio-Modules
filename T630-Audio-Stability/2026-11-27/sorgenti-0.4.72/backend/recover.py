import argparse
from pathlib import Path
from common import recover_rme

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--state',required=True);args=parser.parse_args()
    recover_rme(Path(__file__).resolve().parent,args.state)
