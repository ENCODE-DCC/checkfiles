"""\
Run check and update of md5sum identical files.

Example.

    %(prog)s --username ACCESS_KEY_ID --password SECRET_ACCESS_KEY \\
        --out experiments_statuses.log https://www.encodeproject.org
"""
import datetime
import json
import sys
import subprocess
from collections import defaultdict
from urllib.parse import urljoin
import requests
from slackclient import SlackClient

EPILOG = __doc__

def run(out, url, username, password, bot_token=None, dry_run=False):
    session = requests.Session()
    session.auth = (username, password)
    session.headers['Accept'] = 'application/json'

    dr = ""
    if dry_run:
        dr = "-- Dry Run"

    version = '0.01'

    try:
        ip_output = subprocess.check_output(
            ['hostname'], stderr=subprocess.STDOUT
        ).strip()
        ip = ip_output.decode(errors='replace').rstrip('\n')
    except subprocess.CalledProcessError:
        ip = ''

    initiating_run = (
        'STARTING matching md5sum files detection, version {} ({}) ({}): {} at {}'
    ).format(
        version,
        url,
        dr,
        ip,
        datetime.datetime.now()
    )

    out.write(initiating_run + '\nFile uuid\tmd5sum\tMatching md5sum files\n')
    out.flush()
    if bot_token:
        sc = SlackClient(bot_token)
        sc.api_call(
            "chat.postMessage",
            channel="#bot-reporting",
            text=initiating_run,
            as_user=True,
        )

    graph = []
    r = session.get(
        urljoin(
            url,
            '/search/?type=File&field=external_accession&field=accession&field=uuid&field=status&field=md5sum&field=matching_md5sum&limit=all&format=json'
        )
    )
    try:
        r.raise_for_status()
    except requests.HTTPError:
        return
    else:
        graph = r.json()['@graph']

    accession_to_uuid = {}
    for f in graph:
        if (f.get('accession')):
            accession_to_uuid[f.get('accession')] = f.get('uuid')
        else:
            accession_to_uuid[f.get('external_accession')] = f.get('uuid')

    excluded_statuses = ['uploading', 'upload failed', 'content error']
    md5dictionary = defaultdict(set)
    clashing_dictionary = defaultdict(list)
    for f in graph:
        if f.get('status') not in excluded_statuses:
            md5 = f.get('md5sum')
            if md5:
                md5dictionary[md5].add(f.get('uuid'))
            matching_md5sum = f.get('matching_md5sum')
            if matching_md5sum:
                matching_md5sum_uuids = [
                    entry.split('/')[2] 
                        if (len(entry.split('/')[2]) == 36 
                            and (entry.split('/')[2]).find('-') != -1)
                        else accession_to_uuid.get(entry.split('/')[2]) for entry in matching_md5sum
                ]
                clashing_dictionary[f.get('uuid')] = sorted(matching_md5sum_uuids)
    for key, value in md5dictionary.items():
        if len(value) > 1:
            uuids_list = sorted(list(value))
            for uuid in uuids_list:
                identical_files_list = [
                    entry for entry in uuids_list if entry != uuid
                ]
                if uuid not in clashing_dictionary or sorted(clashing_dictionary[uuid]) != sorted(identical_files_list):
                    item_url = urljoin(url, uuid)
                    data = {
                        "matching_md5sum": identical_files_list,
                    }
                    if not dry_run:
                        r = session.patch(
                            item_url,
                            data=json.dumps(data),
                            headers={
                                'content-type': 'application/json',
                                'accept': 'application/json',
                            },
                        )
                        if not r.ok:
                            print('{} {}\n{}'.format(r.status_code, r.reason, r.text))
                        else:
                            out.write(
                                '{}\tmd5:{}\t{}\n'.format(
                                    uuid,
                                    key,
                                    identical_files_list,
                                )
                            )

                        out.flush()

    finishing_run = 'FINISHED matching md5sum files detection at {}'.format(
        datetime.datetime.now()
    )
    out.write(finishing_run + '\n')
    out.flush()
    out.close()

    if bot_token:
        sc.api_call(
            "chat.postMessage",
            channel="#bot-reporting",
            text=finishing_run,
            as_user=True,
        )


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Update files with identical MD5 values",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--username', '-u', default='', help="HTTP username (access_key_id)"
    )
    parser.add_argument(
        '--bot-token', default='', help="Slack bot token"
    )
    parser.add_argument(
        '--password', '-p',
        default='',
        help="HTTP password (secret_access_key)",
    )
    parser.add_argument(
        '--out', '-o', type=argparse.FileType('w'),
        default=sys.stdout,
        help="file to write json lines of results",
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help="Don't update status, just check",
    )
    parser.add_argument('url', help="server to post to")
    args = parser.parse_args()
    run(**vars(args))


if __name__ == '__main__':
    main()
