"""\
Run status check on experiments.

Example.

    %(prog)s --username ACCESS_KEY_ID --password SECRET_ACCESS_KEY \\
        --out experiments_statuses.log https://www.encodeproject.org
"""
import datetime
import json
import sys
import copy
import os.path
import subprocess
from urllib.parse import urljoin
import requests
from slackclient import SlackClient

EPILOG = __doc__

def run(out, err, url, username, password, search_query, accessions_list=None, bot_token=None, dry_run=False):
    session = requests.Session()
    session.auth = (username, password)
    session.headers['Accept'] = 'application/json'

    dr = ""
    if dry_run:
        dr = "-- Dry Run"

    version = '0.12'

    try:
        ip_output = subprocess.check_output(
            ['hostname'], stderr=subprocess.STDOUT).strip()
        ip = ip_output.decode(errors='replace').rstrip('\n')
    except subprocess.CalledProcessError as e:
        ip = ''

    initiating_run = 'STARTING Checkexperiments version ' + \
        '{} ({}) ({}): {} on {} at {}'.format(
            version, url, search_query, dr, ip, datetime.datetime.now())
    out.write(initiating_run + '\nAward\tAccession\tcurrent status -> new status\tsubmitted date\n')
    out.flush()
    err.write(initiating_run + '\nAward\tAccession\terror message\n')
    err.flush()
    if bot_token:
        sc = SlackClient(bot_token)
        sc.api_call(
            "chat.postMessage",
            channel="#bot-reporting",
            text=initiating_run,
            as_user=True
        )

    minimal_read_depth_requirements = {
        'DNase-seq': 20000000,
        'genetic modification followed by DNase-seq': 20000000,
        'ChIP-seq': 20000000,
        'RAMPAGE': 10000000,
        'shRNA knockdown followed by RNA-seq': 10000000,
        'siRNA knockdown followed by RNA-seq': 10000000,
        'single cell isolation followed by RNA-seq': 10000000,
        'CRISPR genome editing followed by RNA-seq': 10000000,
        'modENCODE-chip': 500000
    }


    graph = []
    # checkexperiments using a file with a list of experiment accessions to be checked
    if accessions_list:
        r = None
        ACCESSIONS = []
        if os.path.isfile(accessions_list):
            ACCESSIONS = [line.rstrip('\n') for line in open(accessions_list)]
        for acc in ACCESSIONS:
            r = session.get(
                urljoin(url, '/search/?field=@id&frame=object&limit=all&type=Experiment&accession=' + acc))
            try:
                r.raise_for_status()
            except requests.HTTPError:
                return
            else:
                local = copy.deepcopy(r.json()['@graph'])
                graph.extend(local)
    # checkexperiments using a query
    else:
        r = session.get(
            urljoin(
                url,
                '/search/?type=Experiment' \
                '&format=json&frame=object&limit=all&' + search_query))
        try:
            r.raise_for_status()
        except requests.HTTPError:
            return
        else:
            graph = r.json()['@graph']

    for ex in graph:
        if ex['status'] != 'started':
            continue
        assay_term_name = ex.get('assay_term_name')
        exp_accession = ex.get('accession')
        award_request = session.get(urljoin(
            url,
            ex.get('award') + '?frame=object&format=json'))
        award_obj = award_request.json()
        award_rfa = award_obj.get('rfa')
        if (
            (assay_term_name not in minimal_read_depth_requirements) or
            (award_rfa == 'modERN') or 
            (award_rfa == 'modENCODE' and assay_term_name != 'ChIP-seq')):
            err.write(
                '{}\t{}\t{}\texcluded from automatic screening\n'.format(
                    award_rfa,
                    assay_term_name,
                    exp_accession)
            )                
            err.flush()
            continue

        try:
            replicates = ex.get('replicates')
            if replicates:
                replicates_set = set()
                submitted_replicates = set()
                replicates_reads = {}
                bio_rep_reads = {}
                replicates_bio_index = {}
                
                for replicate in replicates:
                    replicate_request = session.get(urljoin(
                        url,
                        replicate + '?frame=object&format=json'))
                    replicate_obj = replicate_request.json()
                    if replicate_obj.get('status') not in ['deleted']:
                        replicate_id = replicate_obj.get('@id')
                        replicates_set.add(replicate_id)
                        replicates_reads[replicate_id] = 0
                        replicates_bio_index[replicate_id] = replicate_obj.get('biological_replicate_number')
                        bio_rep_reads[replicates_bio_index[replicate_id]] = 0
                exp_files = ex.get('files')
                if  exp_files:
                    erroneous_status = ['uploading', 'content error', 'upload failed']
                    dates = []
                    for file_acc in exp_files:
                        file_request = session.get(urljoin(
                            url,
                            file_acc + '?frame=object&format=json'))
                        file_obj = file_request.json()
                        if file_obj.get('file_format') == 'fastq' and \
                           file_obj.get('status') not in erroneous_status:
                            replicate_id  = file_obj.get('replicate')
                            read_count = file_obj.get('read_count')
                            if read_count and replicate_id:
                                submitted_replicates.add(replicate_id)
                                if replicate_id in replicates_reads:
                                    run_type = file_obj.get('run_type')
                                    if run_type and run_type == 'paired-ended':
                                        read_count == read_count/2
                                    replicates_reads[replicate_id] += read_count
                                    bio_rep_reads[replicates_bio_index[replicate_id]] += read_count

                                    file_date = datetime.datetime.strptime(
                                        file_obj['date_created'][:10], "%Y-%m-%d")
                                    dates.append(file_date)
                else:
                    continue
            else:
                continue
        except requests.exceptions.RequestException as e:
            print (e)
            continue
        else:
            submitted_flag = True
            if replicates_set and not replicates_set - submitted_replicates:
                key = assay_term_name
                if award_rfa == 'modENCODE':
                    key = 'modENCODE-chip'
                    if assay_term_name in [
                        'DNase-seq',
                        'genetic modification followed by DNase-seq',
                        'ChIP-seq']:
                        replicates_reads = bio_rep_reads
                
                for rep in replicates_reads:
                    if replicates_reads[rep] < minimal_read_depth_requirements[key]:
                        # low read depth in replicate + details
                        submitted_flag = False
                        err.write(
                            '{}\t{}\t{}\t{}\treads_count={}\texpected count={}\n'.format(
                                award_rfa,
                                assay_term_name,
                                exp_accession,
                                rep,
                                replicates_reads[rep],
                                minimal_read_depth_requirements[key])
                        )
                        err.flush()
                        break

                if submitted_flag:
                    pass_audit = True
                    try:
                        audit_request = session.get(urljoin(
                            url,
                            '/' + exp_accession + '?frame=page&format=json'))
                        audit_obj = audit_request.json().get('audit')
                        if audit_obj.get("ERROR"):
                            pass_audit = False
                    except requests.exceptions.RequestException as e:
                        print (e)
                        continue
                    else:
                        if pass_audit:
                            submission_date = max(dates).strftime("%Y-%m-%d")
                            item_url = urljoin(url, exp_accession)
                            data = {
                                "status": "submitted",
                                "date_submitted": submission_date
                            }
                            r = session.patch(
                                item_url,
                                data=json.dumps(data),
                                headers={
                                    'content-type': 'application/json',
                                    'accept': 'application/json'
                                },
                            )
                            if not r.ok:
                                print ('{} {}\n{}'.format(r.status_code, r.reason, r.text))
                            else:
                                out.write(
                                    '{}\t{}\t{}\t{}\t-> submitted\t{}\n'.format(
                                        award_rfa,
                                        assay_term_name,
                                        exp_accession,
                                        ex['status'],
                                        submission_date)
                                )

                            out.flush()
                        else:
                            err.write(
                                '{}\t{}\t{}\taudit errors\n'.format(
                                    award_rfa,
                                    assay_term_name,
                                    exp_accession)
                            )

                            err.flush()

    finishing_run = 'FINISHED Checkexperiments at {}'.format(datetime.datetime.now())
    out.write(finishing_run + '\n')
    out.flush()
    output_filename = out.name
    out.close()
    error_filename = err.name
    err.close()

    if bot_token:
        with open(output_filename, 'r') as output_file:
            sc.api_call("files.upload",
                        title=output_filename,
                        channels='#bot-reporting',
                        content=output_file.read(),
                        as_user=True)

        with open(error_filename, 'r') as output_file:
            sc.api_call("files.upload",
                        title=error_filename,
                        channels='#bot-reporting',
                        content=output_file.read(),
                        as_user=True)

        sc.api_call(
            "chat.postMessage",
            channel="#bot-reporting",
            text=finishing_run,
            as_user=True
        )

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Update experiments status", epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--username', '-u', default='', help="HTTP username (access_key_id)")
    parser.add_argument(
        '--bot-token', default='', help="Slack bot token")
    parser.add_argument(
        '--password', '-p', default='',
        help="HTTP password (secret_access_key)")
    parser.add_argument(
        '--out', '-o', type=argparse.FileType('w'), default=sys.stdout,
        help="file to write json lines of results with or without errors")
    parser.add_argument(
        '--err', '-e', type=argparse.FileType('w'), default=sys.stderr,
        help="file to write json lines of results with errors")
    parser.add_argument(
        '--dry-run', action='store_true', help="Don't update status, just check")
    parser.add_argument(
        '--search-query', default='status=started',
        help="override the experiment search query, e.g. 'accession=ENCSR000ABC'")
    parser.add_argument(
        '--accessions-list', default='',
        help="list of experiment accessions to check")
    parser.add_argument('url', help="server to post to")
    args = parser.parse_args()
    run(**vars(args))


if __name__ == '__main__':
    main()
