import paramiko
from socket import gethostbyname,gaierror
import time
import pytest
# need time.sleep(120) for 2 minutes for initialization. maybe longer?

# pytest to connect and execute commands


def test_deploy():
	demoName = 'circleci-checkfiles-demo'
	try:
		output = subprocess.Popen(['python deploy.py --name {}'.format(demoName)],
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			universal_newlines=True,
			shell=True)
		
		for line in output.stdout.readlines():
			print(line.strip())
			#check ssh connection. 
			# assert running in output #
		print("Waiting for initialization...")
		time.sleep(180) # wait about 2-3 minutes for initialization
	except subprocess.CalledProcessError as e:
		print(e.output.decode(errors='replace').rstrip('\n'))

def test_connection_and_existing_files():
	client = paramiko.SSHClient()
	client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
	privateKey = paramiko.RSAKey.from_private_key_file("/Users/jessica/.ssh/id_rsa")
	client.connect('circleci-checkfiles-demo.instance.encodedcc.org', username='ubuntu', pkey=privateKey)
	commands = ['ls . /opt/']
	for command in commands:
		print("Executing {}".format( command ))
		stdin , stdout, stderr = client.exec_command(command)
		allFiles = stdout.read().decode().split()
	print(allFiles)
	files2check =  ['samtools-1.11', 'ENCODE_CRISPR_Validation', 'encValData', 'GRCh38_no_alt_analysis_set_GCA_000001405.15.fasta', 'encoded']
	assert (set(files2check).issubset(set(allFiles)))
