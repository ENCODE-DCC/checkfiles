Check Files
===========

Files are checked to see if the MD5 sum (both for gzipped and ungzipped) is identical to the submitted metadata, as well as run through
the validateFiles program from jksrc  (http://hgdownload.cse.ucsc.edu/admin/exe/linux.x86_64/validateFiles).
It operates on files in the 'uploading' state (according to the encodeD database) in the encode-files S3 bucket.
Checkfiles is used by the ENCODE DCC to validate genomic datafiles submitted by labs.
The bucket itself is mounted using Goofys (https://github.com/kahing/goofys).
Errors are reported back to encodeD.

Setup
-----

Install pyenv environment(if not already installed from ENCODE-DCC/encoded) repo::

    brew install pyenv
    pyenv install 3.4.3
    pyenv install 2.7.13
    echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.bash_profile
    echo 'export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.bash_profile
    echo 'eval "$(pyenv init -)"' >> ~/.bash_profile
    echo 'eval "pyenv shell 2.7.13 3.4.3"' >> ~/.bash_profile
    source ~/.bash_profile

Install required packages for running deploy::

    pyvenv .
    bin/pip install -r requirements-deploy.txt

Deploy to AWS
-------------

Supply arguments for checkfiles after a ``--`` separator::

    bin/python deploy.py -- --username ACCESS_KEY_ID --password SECRET_ACCESS_KEY --bot-token SLACK-BOT-TOKEN https://www.encodeproject.org

Run on DNAnexus
---------------

Prerequisites
* DNAnexus login
* dx toolkit
* Access to the DNAnexus checkfiles project (everyone in DNAnexus org-cherrylab should have this)
* A DNAnexus path of a file to check in the form project-name:/dir/subdir/filename

To run on a DNAnexus file with --dry-run::

    dx run -i dry_run=t -i dx_file="project-name:/dir/subdir/filename" --watch --yes checkfiles:checkfiles

To capture output and error streams to a file that will be saved to the current DNAnexus project and send those to slack::

    dx run -i dx_file="project-name:/dir/subdir/filename" -i bot_token="mybot-token" -i out="myoutfile" -i err="myerrfile" --watch --yes checkfiles:checkfiles

To see full syntax and options::

    dx run checkfiles:checkfiles --help

NOTE: stdout and stderr are currently sent to the DNAnexus log.  Saving those streams to files it not yet supported.

Deploy to DNAnexus
------------------

You only need to do this if you have changed the code.

If you don't aleady have a DNAnexus login token::

    dx login

Select the checkfiles project on DNAnexus::

    dx select checkfiles

Only if you have changed the checkfiles asset Makefile, in the checkfiles repo root::

    dx build_asset checkfiles_asset

Build the new applet::

    dx build -a checkfiles

