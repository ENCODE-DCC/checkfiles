SHELL=/bin/bash -e

all:
	# Trust the signing key for this repo. Reference: http://cran.rstudio.com/bin/linux/ubuntu/README.html
	sudo apt-key adv --keyserver keyserver.ubuntu.com --recv-keys E084DAB9
	sudo rm -f /etc/apt/apt.conf.d/99dnanexus
	sudo apt-get update
	sudo apt-get -y install python3-pip
	sudo pip3 install slackclient
	sudo curl -sS -L -o /usr/local/bin/validateFiles http://hgdownload.cse.ucsc.edu/admin/exe/linux.x86_64/validateFiles
	sudo chmod +x /usr/local/bin/validateFiles
	sudo mkdir /opt/encValData
	sudo git clone --depth 1 https://github.com/ENCODE-DCC/encValData /opt/encValData
