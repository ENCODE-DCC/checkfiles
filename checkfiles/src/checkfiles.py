#!/usr/bin/env python2
# checkfiles 0.0.1

import common
import os
import sys
import shlex
import subprocess
import dxpy
from pprint import pprint
from copy import deepcopy
import logging

logger = logging.getLogger(__name__)
logger.addHandler(dxpy.DXLogHandler())
logger.propagate = False
logger.setLevel(logging.INFO)

DCC_CREDENTIALS_PROJECT = 'project-F30FzF0048K9JZKxPvB3Y563'
DCC_CREDENTIALS_FOLDER = '/credentials'
KEYFILE = 'keypairs.json'


class PortalCredentialsError(Exception):
    pass


@dxpy.entry_point('main')
def main(**kwargs):

    dxpy.download_folder(
        DCC_CREDENTIALS_PROJECT, '.', folder=DCC_CREDENTIALS_FOLDER)
    if 'key' in kwargs:
        key = '-'.join([dxpy.api.system_whoami()['id'], kwargs.pop('key')])
    else:
        key = dxpy.api.system_whoami()['id']
    key_tuple = common.processkey(key, KEYFILE)
    if not key_tuple:
        logger.error("Key %s is not found in the keyfile %s" % (key, KEYFILE))
        raise PortalCredentialsError("Supply a valid keypair ID")
    authid, authpw, server = key_tuple
    if 'url' in kwargs:
        server = kwargs.pop('url')
    keypair = (authid, authpw)

    tokens = ['python3 checkfiles.py']
    for k, v in kwargs.iteritems():
        if isinstance(v, bool):
            if v:
                tokens.append("--"+k.replace('_', '-'))
            continue
        if isinstance(v, str) or isinstance(v, unicode) or isinstance(v, int):
            tokens.append(' '.join(["--"+k.replace('_', '-'), str(v)]))

    if 'dx_file' in kwargs:
        dxfile = dxpy.DXFile(kwargs.get('dx_file'))
        local_file = dxpy.download_dxfile(dxfile, dxfile.name)
        tokens.append("--local-file %s" % (dxfile.name))

    # this is just to get a command string to print that has no secrets
    tokens_safe = deepcopy(tokens)
    tokens_safe.append("--username %s --password %s" % ("."*len(authid), "."*len(authpw)))
    tokens_safe.append(server)
    logger.info(' '.join(tokens_safe))

    tokens.append("--username %s --password %s" % (authid, authpw))
    # this needs to be the last token
    tokens.append(server)

    checkfiles_command = ' '.join(tokens)
    subprocess.check_call(shlex.split(checkfiles_command))

    output = {}
    outfilename = kwargs.get('out')
    errfilename = kwargs.get('err')
    if outfilename:
        out = dxpy.upload_local_file(outfilename)
        output.update({'out': dxpy.dxlink(out)})
    if errfilename:
        err = dxpy.upload_local_file(errfilename)
        output.update({'err': dxpy.dxlink(err)})

    return output


dxpy.run()
