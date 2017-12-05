#!/usr/bin/env python2
# checkfiles 0.0.1

import common
import os
import sys
import shlex
import subprocess
import dxpy
from pprint import pprint

DCC_CREDENTIALS_PROJECT = 'project-F30FzF0048K9JZKxPvB3Y563'
DCC_CREDENTIALS_FOLDER = '/credentials'
KEYFILE = 'keypairs.json'


@dxpy.entry_point('main')
def main(**kwargs):

    dxpy.download_folder(
        DCC_CREDENTIALS_PROJECT, '.', folder=DCC_CREDENTIALS_FOLDER)
    if 'key' in kwargs:
        key = '-'.join([dxpy.api.system_whoami()['id'], kwargs.get('key')])
    else:
        key = dxpy.api.system_whoami()['id']
    key_tuple = common.processkey(key, KEYFILE)
    assert key_tuple, "ERROR: Key %s is not found in the keyfile %s" % (key, KEYFILE)
    if 'url' in kwargs:
        server = kwargs.pop('url')
    authid, authpw, server = key_tuple
    keypair = (authid, authpw)

    pprint(kwargs)
    tokens = ['python3 checkfiles.py']
    for k, v in kwargs.iteritems():
        if isinstance(v, bool):
            if v:
                tokens.append("--"+k.replace('_', '-'))
            continue
        if isinstance(v, str) or isinstance(v, unicode) or isinstance(v, int):
            tokens.append(' '.join(["--"+k.replace('_', '-'), str(v)]))
    tokens.append("--url %s --username %s --password %s" % (server, authid, authpw))

    if 'dx_file' in kwargs:
        dxfile = dxpy.DXFile(kwargs.get('dx_file'))
        local_file = dxpy.download_dxfile(dxfile, dxfile.name)
        tokens.append("--local-file %s" % (dxfile.name))

    checkfiles_command = ' '.join(tokens)
    print(checkfiles_command)
    subprocess.check_call(shlex.split(checkfiles_command))
    # out = dxpy.upload_local_file("out")
    # err = dxpy.upload_local_file("err")

    output = {}
    # output["out"] = dxpy.dxlink(out)
    # output["err"] = dxpy.dxlink(err)

    return output


dxpy.run()
