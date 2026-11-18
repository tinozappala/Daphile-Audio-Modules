"""Run with the staged interpreter, using the RC5 PyYAML fixture in isolation."""
import bz2
import gzip
import importlib
import importlib.util
import json
import lzma
import os
from pathlib import Path
import py_compile
import socket
import sqlite3
import ssl
import subprocess
import sys
import sysconfig
import tempfile
import zlib

stage, fixture = (Path(p).resolve() for p in sys.argv[1:])
assert sys.version_info[:3] == (3, 13, 18), sys.version
assert sysconfig.get_config_var('Py_ENABLE_SHARED') == 1
assert not sysconfig.get_config_var('Py_GIL_DISABLED')
assert sysconfig.get_config_var('LIBDIR') == '/usr/lib64'
assert sys.platlibdir == 'lib'
lib = stage / 'usr/lib/python3.13'
assert Path(sysconfig.get_path('stdlib')) == lib
modules = 'ssl hashlib sqlite3 ctypes multiprocessing subprocess fcntl readline bz2 lzma zlib _decimal pyexpat dbm.gnu _uuid curses _socket _ssl _sqlite3 _ctypes _curses'.split()
for name in modules:
    module = importlib.import_module(name)
    origin = getattr(module, '__file__', None)
    if origin:
        assert Path(origin).is_relative_to(lib), (name, origin)
for name in 'numpy scipy websocket cffi tkinter yaml ensurepip'.split():
    assert importlib.util.find_spec(name) is None, name
sys.path.insert(0, str(fixture))
import yaml
import yaml._yaml
assert yaml.__version__ == '6.0.2' and yaml.__with_libyaml__
assert Path(yaml._yaml.__file__).is_relative_to(fixture)
assert yaml.CSafeLoader.__module__ == 'yaml.cyaml'
assert yaml.CSafeDumper.__module__ == 'yaml.cyaml'
config = yaml.load('workers: 2\nfeature: true\n', Loader=yaml.CSafeLoader)
assert config == {'workers': 2, 'feature': True}
assert yaml.load(yaml.dump(config, Dumper=yaml.CSafeDumper), Loader=yaml.CSafeLoader) == config
with tempfile.TemporaryDirectory(prefix='daphile-py313-smoke-') as temp:
    temp = Path(temp)
    program = temp / 'program.py'
    program.write_text(
        'import sys, json, hashlib, sqlite3\n'
        'db = sqlite3.connect(":memory:")\n'
        'assert db.execute("SELECT 21 * 2").fetchone() == (42,)\n'
        'print(json.dumps({"version": list(sys.version_info[:3]), "hash": hashlib.sha256(b"fixture").hexdigest()}))\n'
    )
    pyc = temp / 'program.pyc'
    py_compile.compile(str(program), cfile=str(pyc), doraise=True)
    for entry in (program, pyc):
        output = subprocess.check_output([sys.executable, '-I', '-B', str(entry)], timeout=8)
        assert json.loads(output)['version'] == [3, 13, 18]
    code = 'import sys;sys.path.insert(0, sys.argv[1]);import yaml;assert yaml.load("key: 42",Loader=yaml.CSafeLoader)=={"key":42}'
    subprocess.run([sys.executable, '-I', '-B', '-c', code, str(fixture)], check=True, timeout=8)
payload = b'compression fixture\n' * 64
for codec in (gzip, bz2, lzma, zlib):
    assert codec.decompress(codec.compress(payload)) == payload
print(json.dumps({'version': sys.version, 'config': {k: sysconfig.get_config_var(k) for k in
                   ('CONFIG_ARGS', 'LIBDIR', 'SOABI', 'PLATLIBDIR', 'Py_ENABLE_SHARED', 'Py_GIL_DISABLED')},
                  'openssl': ssl.OPENSSL_VERSION, 'sqlite': sqlite3.sqlite_version,
                  'imports': modules, 'pyyaml': {'version': yaml.__version__, 'libyaml': yaml._yaml.get_version_string(),
                                                'loader_dumper_subprocess': 'PASS'},
                  'staged_py_compile_and_program': 'PASS', 'compression': 'PASS'}, indent=2))
