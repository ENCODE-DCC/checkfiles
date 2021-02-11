import boto3
import getpass
import re
import shlex
import subprocess
import sys

from os.path import expanduser


BDM = [
    {
        'DeviceName': '/dev/sda1',
        'Ebs': {
            'VolumeSize': 512,
            'VolumeType': 'gp2',
            'DeleteOnTermination': True
        }
    }
]


def nameify(s):
    name = ''.join(c if c.isalnum() else '-' for c in s.lower()).strip('-')
    return re.subn(r'\-+', '-', name)[0]


def read_ssh_key():
    home = expanduser("~")
    ssh_key_path = home + '/' + '.ssh/id_rsa.pub'
    ssh_keygen_args = ['ssh-keygen', '-l', '-f', ssh_key_path]
    fingerprint = subprocess.check_output(
        ssh_keygen_args
    ).decode('utf-8').strip()
    if fingerprint:
        with open(ssh_key_path, 'r') as f:
            ssh_pub_key = f.readline().strip()
            return ssh_pub_key


def get_user_data(commit, config_file, data_insert, profile_name):
    cmd_list = ['git', 'show', commit + config_file]
    config_template = subprocess.check_output(cmd_list).decode('utf-8')
    ssh_pub_key = read_ssh_key()
    if not ssh_pub_key:
        print(
            "WARNING: User is not authorized with ssh access to "
            "new instance because they have no ssh key"
        )
    data_insert['LOCAL_SSH_KEY'] = ssh_pub_key
    # aws s3 authorized_keys folder
    auth_base = 's3://encoded-conf-prod/ssh-keys'
    auth_type = 'prod'
    if profile_name != 'production':
        auth_type = 'demo'
    auth_keys_dir = '{auth_base}/{auth_type}-authorized_keys'.format(
        auth_base=auth_base,
        auth_type=auth_type,
    )
    data_insert['S3_AUTH_KEYS'] = auth_keys_dir
    user_data = config_template % data_insert
    return user_data


def run(image_id, instance_type,
        branch=None, name=None, profile_name=None, args=()):
    if branch is None:
        branch = subprocess.check_output(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD']
            ).decode('utf-8').strip()

    commit = subprocess.check_output(
        ['git', 'rev-parse', '--short', branch]).decode('utf-8').strip()

    if not subprocess.check_output(
            ['git', 'branch', '-r', '--contains', commit]).strip():
        print("Commit %r not in origin. Did you git push?" % commit)
        sys.exit(1)

    username = getpass.getuser()

    if name is None:
        name = nameify('checkfiles-%s-%s-%s' % (branch, commit, username))

    session = boto3.Session(region_name='us-west-2', profile_name=profile_name)
    ec2 = session.resource('ec2')

    domain = 'production' if profile_name == 'production' else 'instance'

    if any(ec2.instances.filter(
            Filters=[
                {'Name': 'tag:Name', 'Values': [name]},
                {'Name': 'instance-state-name',
                 'Values': ['pending', 'running', 'stopping', 'stopped']},
            ])):
        print('An instance already exists with name: %s' % name)
        sys.exit(1)
    # Add template data to cloud config file
    config_file = ':cloud-config.yml'
    data_insert = {
        'COMMIT': commit,
        'ARGS': ' '.join(shlex.quote(arg) for arg in args),
    }
    user_data = get_user_data(commit, config_file, data_insert, 'demo')
    reservation = ec2.create_instances(
        MinCount=1,
        MaxCount=1,
        ImageId=image_id,
        InstanceType=instance_type,
        SecurityGroups=['ssh-http-https'],
        BlockDeviceMappings=BDM,
        UserData=user_data,
        InstanceInitiatedShutdownBehavior='terminate',
        IamInstanceProfile={'Name': 'encoded-instance'},
    )

    instance = reservation[0]  # Instance:i-34edd56f
    print('%s.%s.encodedcc.org' % (instance.instance_id, domain))
    instance.wait_until_exists()
    instance.create_tags(Tags=[
        {'Key': 'Name', 'Value': name},
        {'Key': 'branch', 'Value': branch},
        {'Key': 'commit', 'Value': commit},
        {'Key': 'started_by', 'Value': username},
    ])
    print('ssh ubuntu@%s.%s.encodedcc.org' % (name, domain))
    print('pending...')
    instance.wait_until_running()
    print(instance.state['Name'])


def main():
    import argparse

    def hostname(value):
        if value != nameify(value):
            raise argparse.ArgumentTypeError(
                "%r is an invalid hostname, only [a-z0-9] and hyphen allowed."
                % value)
        return value

    parser = argparse.ArgumentParser(
        description="Deploy checkfiles on AWS",
    )
    parser.add_argument(
        '-b', '--branch', default=None, help="Git branch or tag")
    parser.add_argument(
        '-n', '--name', type=hostname, help="Instance name")
    parser.add_argument(
        '--image-id', default='ami-0a00ce72',
        help="ubuntu/images/hvm-ssd/ubuntu-xenial-16.04-amd64-server-20171026.1")
    parser.add_argument(
        '--instance-type', default='c5.2xlarge',
        help="specify 'c5.9xlarge' if there are many files to check")
    parser.add_argument(
        '--profile-name', default=None, help="AWS creds profile")
    parser.add_argument(
        'args', metavar='ARG', nargs='*', help="arguments for checkfiles")
    args = parser.parse_args()

    return run(**vars(args))


if __name__ == '__main__':
    main()
