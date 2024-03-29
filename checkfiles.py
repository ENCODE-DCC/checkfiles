"""\
Run sanity checks on files.

Example.

    %(prog)s --username ACCESS_KEY_ID --password SECRET_ACCESS_KEY \\
        --output check_files.log https://www.encodeproject.org
"""
import datetime
import time
import os.path
import json
import sys
from shlex import quote
import subprocess
import re
from urllib.parse import urljoin
import requests
import copy
from slackclient import SlackClient

EPILOG = __doc__

PYTHON_PATH = "/opt/encoded/checkfiles/venv/bin/python"

# For submitters, bam files should not be submitted as .gz
GZIP_TYPES = [
    "CEL",
    "bam",
    "bed",
    "bedpe",
    "csfasta",
    "csqual",
    "fasta",
    "fastq",
    "gff",
    "gtf",
    "tagAlign",
    "tar",
    "txt",
    "sam",
    "wig",
    "vcf",
    "pairs",
]

read_name_prefix = re.compile(
    '^(@[a-zA-Z\d]+[a-zA-Z\d_-]*:[a-zA-Z\d-]+:[a-zA-Z\d_-]' +
    '+:\d+:\d+:\d+:\d+)$')

read_name_pattern = re.compile(
    '^(@[a-zA-Z\d]+[a-zA-Z\d_-]*:[a-zA-Z\d-]+:[a-zA-Z\d_-]' +
    '+:\d+:\d+:\d+:\d+[\s_][123]:[YXN]:[0-9]+:([ACNTG\+]*|[0-9]*))$'
)

special_read_name_pattern = re.compile(
    '^(@[a-zA-Z\d]+[a-zA-Z\d_-]*:[a-zA-Z\d-]+:[a-zA-Z\d_-]' +
    '+:\d+:\d+:\d+:\d+[/1|/2]*[\s_][123]:[YXN]:[0-9]+:([ACNTG\+]*|[0-9]*))$'
)

srr_read_name_pattern = re.compile(
    '^(@SRR[\d.]+)$'
)

pacbio_read_name_pattern = re.compile(
    '^(@m\d{6}_\d{6}_\d+_[a-zA-Z\d_-]+\/.*)$|^(@m\d+U?_\d{6}_\d{6}\/.*)$|^(@c.+)$'
)

def is_path_gzipped(path):
    with open(path, 'rb') as f:
        magic_number = f.read(2)
    return magic_number == b'\x1f\x8b'


def update_content_error(errors, error_message):
    if 'content_error' not in errors:
        errors['content_error'] = error_message
    else:
        errors['content_error'] += ', ' + error_message


def check_format(encValData, job, path):
    """ Local validation
    """
    ASSEMBLY_MAP = {
        'GRCh38-minimal': 'GRCh38',
        'mm10-minimal': 'mm10'
    }

    errors = job['errors']
    item = job['item']
    result = job['result']
    subreads = False

    # if assembly is in the map, use the mapping, otherwise just use the string in assembly
    assembly = ASSEMBLY_MAP.get(item.get('assembly'), item.get('assembly'))
    file_output_type = item.get('output_type')
    if (item.get('file_format') == 'bam' and 
        file_output_type in ['transcriptome alignments',
                             'gene alignments',
                             'redacted transcriptome alignments']):
        if 'assembly' not in item:
            errors['assembly'] = 'missing assembly'
            update_content_error(errors, 'File metadata lacks assembly information')
        if 'genome_annotation' not in item:
            errors['genome_annotation'] = 'missing genome_annotation'
            update_content_error(errors, 'File metadata lacks genome annotation information')
        if errors:
            return errors
        if file_output_type in ['transcriptome alignments', 'redacted transcriptome alignments']:
            chromInfo = '-chromInfo={}/{}/{}/chrom.sizes'.format(
                encValData, assembly, item['genome_annotation'])
        else:
            chromInfo = '-chromInfo={}/{}/{}/gene.sizes'.format(
                encValData, assembly, item['genome_annotation'])
    elif (item.get('file_format') == 'bam' and 
        file_output_type in ['subreads']):
        subreads = True
        chromInfo = ''
    else:
        chromInfo = '-chromInfo={}/{}/chrom.sizes'.format(encValData, assembly)

    validate_map = {
        ('fasta', None): ['-type=fasta'],
        ('fastq', None): ['-type=fastq'],
        ('bam', None): ['-type=bam', chromInfo],
        ('bigWig', None): ['-type=bigWig', chromInfo],
        ('bigInteract', None): ['-type=bigBed5+13', chromInfo, '-as=%s/as/interact.as' % encValData],
        # standard bed formats
        ('bed', 'bed3'): ['-type=bed3', chromInfo],
        ('bigBed', 'bed3'): ['-type=bigBed3', chromInfo],
        ('bed', 'bed5'): ['-type=bed5', chromInfo],
        ('bigBed', 'bed5'): ['-type=bigBed5', chromInfo],
        ('bed', 'bed6'): ['-type=bed6', chromInfo],
        ('bigBed', 'bed6'): ['-type=bigBed6', chromInfo],
        ('bed', 'bed9'): ['-type=bed9', chromInfo],
        ('bigBed', 'bed9'): ['-type=bigBed9', chromInfo],
        ('bedGraph', None): ['-type=bedGraph', chromInfo],
        # extended "bed+" formats, -tab is required to allow for text fields to contain spaces
        ('bed', 'bed3+'): ['-tab', '-type=bed3+', chromInfo],
        ('bigBed', 'bed3+'): ['-tab', '-type=bigBed3+', chromInfo],
        ('bed', 'bed6+'): ['-tab', '-type=bed6+', chromInfo],
        ('bigBed', 'bed6+'): ['-tab', '-type=bigBed6+', chromInfo],
        ('bed', 'bed9+'): ['-tab', '-type=bed9+', chromInfo],
        ('bigBed', 'bed9+'): ['-tab', '-type=bigBed9+', chromInfo],
        # a catch-all shoe-horn (as long as it's tab-delimited)
        ('bed', 'unknown'): ['-tab', '-type=bed3+', chromInfo],
        ('bigBed', 'unknown'): ['-tab', '-type=bigBed3+', chromInfo],
        # special bed types
        ('bed', 'bedLogR'): ['-type=bed9+1', chromInfo, '-as=%s/as/bedLogR.as' % encValData],
        ('bigBed', 'bedLogR'): ['-type=bigBed9+1', chromInfo, '-as=%s/as/bedLogR.as' % encValData],
        ('bed', 'bedMethyl'): ['-type=bed9+2', chromInfo, '-as=%s/as/bedMethyl.as' % encValData],
        ('bigBed', 'bedMethyl'): ['-type=bigBed9+2', chromInfo, '-as=%s/as/bedMethyl.as' % encValData],
        ('bed', 'broadPeak'): ['-type=bed6+3', chromInfo, '-as=%s/as/broadPeak.as' % encValData],
        ('bigBed', 'broadPeak'): ['-type=bigBed6+3', chromInfo, '-as=%s/as/broadPeak.as' % encValData],
        ('bed', 'gappedPeak'): ['-type=bed12+3', chromInfo, '-as=%s/as/gappedPeak.as' % encValData],
        ('bigBed', 'gappedPeak'): ['-type=bigBed12+3', chromInfo, '-as=%s/as/gappedPeak.as' % encValData],
        ('bed', 'narrowPeak'): ['-type=bed6+4', chromInfo, '-as=%s/as/narrowPeak.as' % encValData],
        ('bigBed', 'narrowPeak'): ['-type=bigBed6+4', chromInfo, '-as=%s/as/narrowPeak.as' % encValData],
        ('bed', 'bedRnaElements'): ['-type=bed6+3', chromInfo, '-as=%s/as/bedRnaElements.as' % encValData],
        ('bigBed', 'bedRnaElements'): ['-type=bed6+3', chromInfo, '-as=%s/as/bedRnaElements.as' % encValData],
        ('bed', 'bedExonScore'): ['-type=bed6+3', chromInfo, '-as=%s/as/bedExonScore.as' % encValData],
        ('bigBed', 'bedExonScore'): ['-type=bigBed6+3', chromInfo, '-as=%s/as/bedExonScore.as' % encValData],
        ('bed', 'bedRrbs'): ['-type=bed9+2', chromInfo, '-as=%s/as/bedRrbs.as' % encValData],
        ('bigBed', 'bedRrbs'): ['-type=bigBed9+2', chromInfo, '-as=%s/as/bedRrbs.as' % encValData],
        ('bed', 'enhancerAssay'): ['-type=bed9+1', chromInfo, '-as=%s/as/enhancerAssay.as' % encValData],
        ('bigBed', 'enhancerAssay'): ['-type=bigBed9+1', chromInfo, '-as=%s/as/enhancerAssay.as' % encValData],
        ('bed', 'modPepMap'): ['-type=bed9+7', chromInfo, '-as=%s/as/modPepMap.as' % encValData],
        ('bigBed', 'modPepMap'): ['-type=bigBed9+7', chromInfo, '-as=%s/as/modPepMap.as' % encValData],
        ('bed', 'pepMap'): ['-type=bed9+7', chromInfo, '-as=%s/as/pepMap.as' % encValData],
        ('bigBed', 'pepMap'): ['-type=bigBed9+7', chromInfo, '-as=%s/as/pepMap.as' % encValData],
        ('bed', 'openChromCombinedPeaks'): ['-type=bed9+12', chromInfo, '-as=%s/as/openChromCombinedPeaks.as' % encValData],
        ('bigBed', 'openChromCombinedPeaks'): ['-type=bigBed9+12', chromInfo, '-as=%s/as/openChromCombinedPeaks.as' % encValData],
        ('bed', 'peptideMapping'): ['-type=bed6+4', chromInfo, '-as=%s/as/peptideMapping.as' % encValData],
        ('bigBed', 'peptideMapping'): ['-type=bigBed6+4', chromInfo, '-as=%s/as/peptideMapping.as' % encValData],
        ('bed', 'shortFrags'): ['-type=bed6+21', chromInfo, '-as=%s/as/shortFrags.as' % encValData],
        ('bigBed', 'shortFrags'): ['-type=bigBed6+21', chromInfo, '-as=%s/as/shortFrags.as' % encValData],
        ('bed', 'encode_elements_H3K27ac'): ['-tab', '-type=bed9+1', chromInfo, '-as=%s/as/encode_elements_H3K27ac.as' % encValData],
        ('bigBed', 'encode_elements_H3K27ac'): ['-tab', '-type=bigBed9+1', chromInfo, '-as=%s/as/encode_elements_H3K27ac.as' % encValData],
        ('bed', 'encode_elements_H3K9ac'): ['-tab', '-type=bed9+1', chromInfo, '-as=%s/as/encode_elements_H3K9ac.as' % encValData],
        ('bigBed', 'encode_elements_H3K9ac'): ['-tab', '-type=bigBed9+1', chromInfo, '-as=%s/as/encode_elements_H3K9ac.as' % encValData],
        ('bed', 'encode_elements_H3K4me1'): ['-tab', '-type=bed9+1', chromInfo, '-as=%s/as/encode_elements_H3K4me1.as' % encValData],
        ('bigBed', 'encode_elements_H3K4me1'): ['-tab', '-type=bigBed9+1', chromInfo, '-as=%s/as/encode_elements_H3K4me1.as' % encValData],
        ('bed', 'encode_elements_H3K4me3'): ['-tab', '-type=bed9+1', chromInfo, '-as=%s/as/encode_elements_H3K4me3.as' % encValData],
        ('bigBed', 'encode_elements_H3K4me3'): ['-tab', '-type=bigBed9+1', chromInfo, '-as=%s/as/encode_elements_H3K4me3.as' % encValData],
        ('bed', 'dnase_master_peaks'): ['-tab', '-type=bed9+1', chromInfo, '-as=%s/as/dnase_master_peaks.as' % encValData],
        ('bigBed', 'dnase_master_peaks'): ['-tab', '-type=bigBed9+1', chromInfo, '-as=%s/as/dnase_master_peaks.as' % encValData],
        ('bed', 'encode_elements_dnase_tf'): ['-tab', '-type=bed5+1', chromInfo, '-as=%s/as/encode_elements_dnase_tf.as' % encValData],
        ('bigBed', 'encode_elements_dnase_tf'): ['-tab', '-type=bigBed5+1', chromInfo, '-as=%s/as/encode_elements_dnase_tf.as' % encValData],
        ('bed', 'candidate enhancer predictions'): ['-type=bed3+', chromInfo, '-as=%s/as/candidate_enhancer_prediction.as' % encValData],
        ('bigBed', 'candidate enhancer predictions'): ['-type=bigBed3+', chromInfo, '-as=%s/as/candidate_enhancer_prediction.as' % encValData],
        ('bed', 'enhancer predictions'): ['-type=bed3+', chromInfo, '-as=%s/as/enhancer_prediction.as' % encValData],
        ('bigBed', 'enhancer predictions'): ['-type=bigBed3+', chromInfo, '-as=%s/as/enhancer_prediction.as' % encValData],
        ('bed', 'idr_peak'): ['-type=bed6+', chromInfo, '-as=%s/as/idr_peak.as' % encValData],
        ('bigBed', 'idr_peak'): ['-type=bigBed6+', chromInfo, '-as=%s/as/idr_peak.as' % encValData],
        ('bed', 'tss_peak'): ['-type=bed6+', chromInfo, '-as=%s/as/tss_peak.as' % encValData],
        ('bigBed', 'tss_peak'): ['-type=bigBed6+', chromInfo, '-as=%s/as/tss_peak.as' % encValData],
        ('bed', 'idr_ranked_peak'): ['-type=bed6+14', chromInfo, '-as=%s/as/idr_ranked_peak.as' % encValData],
        ('bed', 'element enrichments'): ['-type=bed6+5', chromInfo, '-as=%s/as/mpra_starr.as' % encValData],
        ('bigBed', 'element enrichments'): ['-type=bigBed6+5', chromInfo, '-as=%s/as/mpra_starr.as' % encValData],
        ('bed', 'CRISPR element quantifications'): ['-type=bed3+22', chromInfo, '-as=%s/as/element_quant_format.as' % encValData],
        
        ('bedpe', None): ['-type=bed3+', chromInfo],
        ('bedpe', 'mango'): ['-type=bed3+', chromInfo],
        # non-bed types
        ('rcc', None): ['-type=rcc'],
        ('idat', None): ['-type=idat'],
        ('gtf', None): None,
        ('tagAlign', None): ['-type=tagAlign', chromInfo],
        ('tar', None): None,
        ('tsv', None): None,
        ('csv', None): None,
        ('2bit', None): None,
        ('csfasta', None): ['-type=csfasta'],
        ('csqual', None): ['-type=csqual'],
        ('CEL', None): None,
        ('sam', None): None,
        ('wig', None): None,
        ('hdf5', None): None,
        ('hic', None): None,
        ('gff', None): None,
        ('vcf', None): None,
        ('btr', None): None
    }

    if not subreads:
        # samtools quickcheck
        if item.get('file_format') == 'bam':
            try:
                output = subprocess.check_output(
                    ['samtools', 'quickcheck', path], stderr=subprocess.STDOUT)
            except subprocess.CalledProcessError as e:
                errors['bamValidation'] = e.output.decode(errors='replace').rstrip('\n')
                update_content_error(errors, 'File failed bam validation ' +
                                            '(samtools quickcheck). ' + errors['bamValidation'])
            else:
                result['bamValidation'] = output.decode(errors='replace').rstrip('\n')

        # validateFiles
        validate_args = validate_map.get((item['file_format'], item.get('file_format_type')))
        if validate_args is None:
            return

        if chromInfo in validate_args and 'assembly' not in item:
            errors['assembly'] = 'missing assembly'
            update_content_error(errors, 'File metadata lacks assembly information')
            return

        result['validateFiles_args'] = ' '.join(validate_args)

    
        try:
            output = subprocess.check_output(
                ['validateFiles'] + validate_args + [path], stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            errors['validateFiles'] = e.output.decode(errors='replace').rstrip('\n')
            update_content_error(errors, 'File failed file format specific ' +
                                         'validation (encValData) ' + errors['validateFiles'])
        else:
            result['validateFiles'] = output.decode(errors='replace').rstrip('\n')

def validate_crispr(job, filePath):
    '''
    ENCODE CRISPR Group provided scripts for guide quantification validation
    which can be found here: https://github.com/oh-jinwoo94/ENCODE by Jin Woo Oh 
    '''
    errors = job['errors']
    item = job['item']
    result = job['result']

    guide_validationScript_path = '/opt/ENCODE_CRISPR_Validation/check_guide_quant_format.py'
    pam_validationScript_path =  '/opt/ENCODE_CRISPR_Validation/check_PAM.py'
    guide_format_path = '/opt/ENCODE_CRISPR_Validation/guide_quant_format.txt'
    genome_reference_path  = '/opt/GRCh38_no_alt_analysis_set_GCA_000001405.15.fasta'

    try:
        output = subprocess.Popen(  
                            [PYTHON_PATH, guide_validationScript_path,
                            guide_format_path, filePath],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            universal_newlines=True)
        
        checkPAM = False
        for line in output.stdout.readlines():
            line = line.strip()
            
            try:
                assert('passed' in line)
                checkPAM = True

            except AssertionError:
                errors['CRISPR_guide_quant_validation'] = line
                update_content_error(errors, 'File failed CRISPR guide quantification format validation ' +
                                            '(check_guide_quant_format.py). ' + errors['CRISPR_guide_quant_validation'])
            else:
                result['CRISPR_guide_quant_validation'] = line
      
        if checkPAM:
            try:
                output = subprocess.Popen(  
                                [PYTHON_PATH, pam_validationScript_path,
                                filePath,
                                genome_reference_path],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                universal_newlines=True)

                count = 0
                for line in output.stdout.readlines():
                    line  = line.strip()
                    if count == 3:
                        try:
                            assert('More than 80% of the PAMs are NGG. The coordinates are likely to be correct' in line)

                        except AssertionError:
                            errors['CRISPR_PAM_validation'] = line
                            update_content_error(errors, 'File failed CRISPR PAM validation ' +
                                            '(check_PAM.py). ' + errors['CRISPR_PAM_validation'])
                        else:
                            result['CRISPR_PAM_validation'] = line
                    count+=1

            except subprocess.CalledProcessError as e:
                errors['CRISPR_PAM_info_extraction'] = 'Failed to extract information from ' + \
                                                            local_path
            
    except subprocess.CalledProcessError as e:
        errors['CRISPR_guide_info_extraction'] = 'Failed to extract information from ' + \
                                                            local_path
    

def process_illumina_read_name_pattern(read_name,
                                       read_numbers_set,
                                       signatures_set,
                                       signatures_no_barcode_set,
                                       srr_flag):
    read_name_array = re.split(r'[:\s_]', read_name)
    flowcell = read_name_array[2]
    lane_number = read_name_array[3]
    if srr_flag:
        read_number = list(read_numbers_set)[0]
    else:
        read_number = read_name_array[-4]
        read_numbers_set.add(read_number)
    barcode_index = read_name_array[-1]
    signatures_set.add(
        flowcell + ':' + lane_number + ':' +
        read_number + ':' + barcode_index + ':')
    signatures_no_barcode_set.add(
        flowcell + ':' + lane_number + ':' +
        read_number + ':')


def process_special_read_name_pattern(read_name,
                                      words_array,
                                      signatures_set,
                                      signatures_no_barcode_set,
                                      read_numbers_set,
                                      srr_flag):
    if srr_flag:
        read_number = list(read_numbers_set)[0]
    else:
        read_number = 'not initialized'
        if len(words_array[0]) > 3 and \
           words_array[0][-2:] in ['/1', '/2']:
            read_number = words_array[0][-1]
            read_numbers_set.add(read_number)
    read_name_array = re.split(r'[:\s_]', read_name)
    flowcell = read_name_array[2]
    lane_number = read_name_array[3]
    barcode_index = read_name_array[-1]
    signatures_set.add(
        flowcell + ':' + lane_number + ':' +
        read_number + ':' + barcode_index + ':')
    signatures_no_barcode_set.add(
        flowcell + ':' + lane_number + ':' +
        read_number + ':')


def process_new_illumina_prefix(read_name,
                                signatures_set,
                                old_illumina_current_prefix,
                                read_numbers_set,
                                srr_flag):
    if srr_flag:
        read_number = list(read_numbers_set)[0]
    else:
        read_number = '1'
        read_numbers_set.add(read_number)
    read_name_array = re.split(r':', read_name)

    if len(read_name_array) > 3:
        flowcell = read_name_array[2]
        lane_number = read_name_array[3]

        prefix = flowcell + ':' + lane_number
        if prefix != old_illumina_current_prefix:
            old_illumina_current_prefix = prefix

            signatures_set.add(
                flowcell + ':' + lane_number + ':' +
                read_number + '::' + read_name)

    return old_illumina_current_prefix


def process_pacbio_read_name_pattern(
        read_name,
        signatures_set,
        movie_identifier
        ):
    arr = re.split(r'/', read_name)
    if len(arr) > 1:
        movie_identifier = arr[0]
        signatures_set.add(
            'pacbio:0:1::' + movie_identifier)
    return movie_identifier


def process_old_illumina_read_name_pattern(read_name,
                                           read_numbers_set,
                                           signatures_set,
                                           old_illumina_current_prefix,
                                           srr_flag):
    if srr_flag:
        read_number = list(read_numbers_set)[0]
    else:
        read_number = '1'
        if read_name[-2:] in ['/1', '/2']:
            read_numbers_set.add(read_name[-1])
            read_number = read_name[-1]
    arr = re.split(r':', read_name)
    if len(arr) > 1:
        prefix = arr[0] + ':' + arr[1]
        if prefix != old_illumina_current_prefix:
            old_illumina_current_prefix = prefix
            flowcell = arr[0][1:]
            if (flowcell.find('-') != -1 or
               flowcell.find('_') != -1):
                flowcell = 'TEMP'
            # at this point we assume the read name is following old illumina format template
            # however sometimes the read names are following some different template
            # in case the lane variable is different from number (i.e contains letters)
            # we will default it to 0, the information is not lost, since the whole read name is
            # at the end of the signature string
            lane_number = '0'
            if arr[1].isdigit():
                lane_number = arr[1]
            signatures_set.add(
                flowcell + ':' + lane_number + ':' +
                read_number + '::' + read_name)

    return old_illumina_current_prefix


def process_read_name_line(read_name_line,
                           old_illumina_current_prefix,
                           read_numbers_set,
                           signatures_no_barcode_set,
                           signatures_set,
                           read_lengths_dictionary,
                           errors, srr_flag, read_name_details):
    read_name = read_name_line.strip()
    if read_name_details:
        #extract fastq signature parts using read_name_detail
        read_name_array = re.split(r'[:\s]', read_name)

        flowcell = read_name_array[read_name_details['flowcell_id_location']]
        lane_number = read_name_array[read_name_details['lane_id_location']]
        if not read_name_details.get('read_number_location'):
            read_number = "1"
        else:
            read_number = read_name_array[read_name_details['read_number_location']]
        read_numbers_set.add(read_number)
        
        if not read_name_details.get('barcode_location'):
            barcode_index = ''
        else:
            barcode_index = read_name_array[read_name_details['barcode_location']]
        
        signatures_set.add(
            flowcell + ':' + lane_number + ':' +
            read_number + ':' + barcode_index + ':')
        signatures_no_barcode_set.add(
            flowcell + ':' + lane_number + ':' +
            read_number + ':')
    else:
        words_array = re.split(r'\s', read_name)
        if read_name_pattern.match(read_name) is None:
            if special_read_name_pattern.match(read_name) is not None:
                process_special_read_name_pattern(read_name,
                                                words_array,
                                                signatures_set,
                                                signatures_no_barcode_set,
                                                read_numbers_set,
                                                srr_flag)
            elif srr_read_name_pattern.match(read_name.split(' ')[0]) is not None:
                # in case the readname is following SRR format, read number will be
                # defined using SRR format specifications, and not by the illumina portion of the read name
                # srr_flag is used to distinguish between srr and "regular" readname formats
                srr_portion = read_name.split(' ')[0]
                if srr_portion.count('.') == 2:
                    read_numbers_set.add(srr_portion[-1])
                else:
                    read_numbers_set.add('1')
                illumina_portion = read_name.split(' ')[1]
                old_illumina_current_prefix = process_read_name_line('@'+illumina_portion,
                                                                    old_illumina_current_prefix,
                                                                    read_numbers_set,
                                                                    signatures_no_barcode_set,
                                                                    signatures_set,
                                                                    read_lengths_dictionary,
                                                                    errors, True, read_name_details)
            elif pacbio_read_name_pattern.match(read_name):
                # pacbio reads include: 
                # movie identifier that includes the time of run start (m140415_143853)
                # instrment serial number (42175)
                # SMRT cell barcode (c100635972550000001823121909121417)
                # set number
                # part number
                # m140415_143853_42175_c100635972550000001823121909121417_s1_p0/....
                # alternatively the names would include:
                # instrment serial number (42175)
                # time of run start (140415_143853)
                # m42175_140415_143853/
                movie_identifier = read_name.split('/')[0]
                if len(movie_identifier) > 0:
                    process_pacbio_read_name_pattern(
                        read_name,
                        signatures_set,
                        movie_identifier
                    )
                else:
                    errors['fastq_format_readname'] = read_name   
            else:
                # unrecognized read_name_format
                # current convention is to include WHOLE
                # readname at the end of the signature
                if len(words_array) == 1:
                    if read_name_prefix.match(read_name) is not None:
                        # new illumina without second part
                        old_illumina_current_prefix = process_new_illumina_prefix(
                            read_name,
                            signatures_set,
                            old_illumina_current_prefix,
                            read_numbers_set,
                            srr_flag)

                    elif len(read_name) > 3 and read_name.count(':') > 2:
                        # assuming old illumina format
                        old_illumina_current_prefix = process_old_illumina_read_name_pattern(
                            read_name,
                            read_numbers_set,
                            signatures_set,
                            old_illumina_current_prefix,
                            srr_flag)
                    else:
                        errors['fastq_format_readname'] = read_name
                        # the only case to skip update content error - due to the changing
                        # nature of read names
                else:
                    errors['fastq_format_readname'] = read_name
        # found a match to the regex of "almost" illumina read_name
        else:
            process_illumina_read_name_pattern(
                read_name,
                read_numbers_set,
                signatures_set,
                signatures_no_barcode_set,
                srr_flag)

    return old_illumina_current_prefix


def process_sequence_line(sequence_line, read_lengths_dictionary):
    length = len(sequence_line.strip())
    if length not in read_lengths_dictionary:
        read_lengths_dictionary[length] = 0
    read_lengths_dictionary[length] += 1


def process_fastq_file(job, fastq_data_stream, session, url):
    item = job['item']
    errors = job['errors']
    result = job['result']

    platform_uuid = get_platform_uuid(job.get('@id'), errors, session, url)
    read_name_details = get_read_name_details(job.get('@id'), errors, session, url)

    read_numbers_set = set()
    signatures_set = set()
    signatures_no_barcode_set = set()
    read_lengths_dictionary = {}
    read_count = 0
    old_illumina_current_prefix = 'empty'
    try:
        line_index = 0
        for encoded_line in fastq_data_stream.stdout:
            try:
                line = encoded_line.decode('utf-8')
            except UnicodeDecodeError:
                errors['readname_encoding'] = 'Error occured, while decoding the readname string.'
            else:
                line_index += 1
                if line_index == 1:
                    
                    # may be from here deliver a flag about the presence/absence of the readnamedetails

                    if platform_uuid not in ['25acccbd-cb36-463b-ac96-adbac11227e6']:
                        old_illumina_current_prefix = \
                            process_read_name_line(
                                line,
                                old_illumina_current_prefix,
                                read_numbers_set,
                                signatures_no_barcode_set,
                                signatures_set,
                                read_lengths_dictionary,
                                errors, False,
                                read_name_details)
                if line_index == 2:
                    read_count += 1
                    process_sequence_line(line, read_lengths_dictionary)

                line_index = line_index % 4
    except IOError:
        errors['unzipped_fastq_streaming'] = 'Error occured, while streaming unzipped fastq.'
    else:

        # read_count update
        result['read_count'] = read_count

        # read1/read2
        # Ultima FASTQs should be excluded from read pairing checks
        if platform_uuid not in ['25acccbd-cb36-463b-ac96-adbac11227e6']:
            if len(read_numbers_set) > 1:
                errors['inconsistent_read_numbers'] = \
                    'fastq file contains mixed read numbers ' + \
                    '{}.'.format(', '.join(sorted(list(read_numbers_set))))
                update_content_error(errors,
                                     'Fastq file contains a mixture of read1 and read2 sequences')

        # read_length
        read_lengths_list = []
        for k in sorted(read_lengths_dictionary.keys()):
            read_lengths_list.append((k, read_lengths_dictionary[k]))

        #excluding Pacbio, Nanopore, and Ultima from read_length verification
        if platform_uuid not in ['ced61406-dcc6-43c4-bddd-4c977cc676e8',
                                 'c7564b38-ab4f-4c42-a401-3de48689a998',
                                 'e2be5728-5744-4da4-8881-cb9526d0389e',
                                 '7cc06b8c-5535-4a77-b719-4c23644e767d',
                                 '8f1a9a8c-3392-4032-92a8-5d196c9d7810',
                                 '6c275b37-018d-4bf8-85f6-6e3b830524a9',
                                 '6ce511d5-eeb3-41fc-bea7-8c38301e88c1',
                                 '25acccbd-cb36-463b-ac96-adbac11227e6'
                                 ]:
            if 'read_length' in item and item['read_length'] > 2:
                process_read_lengths(read_lengths_dictionary,
                                     read_lengths_list,
                                     item['read_length'],
                                     read_count,
                                     0.9,
                                     errors,
                                     result)
            else:
                errors['read_length'] = 'no specified read length in the uploaded fastq file, ' + \
                                        'while read length(s) found in the file were {}. '.format(
                                            ', '.join(map(str, read_lengths_list)))
                update_content_error(errors,
                                     'Fastq file metadata lacks read length information, ' +
                                     'but the file contains read length(s) {}'.format(
                                         ', '.join(map(str, read_lengths_list))))
        # signatures
        # Ultima FASTQs should be excluded from signature checks
        if platform_uuid in ['25acccbd-cb36-463b-ac96-adbac11227e6']:
            return
        signatures_for_comparison = set()
        is_UMI = False
        if 'flowcell_details' in item and len(item['flowcell_details']) > 0:
            for entry in item['flowcell_details']:
                if 'barcode' in entry and entry['barcode'] == 'UMI':
                    is_UMI = True
                    break
        if old_illumina_current_prefix == 'empty' and is_UMI:
            for entry in signatures_no_barcode_set:
                signatures_for_comparison.add(entry + 'UMI:')
        else:
            if old_illumina_current_prefix == 'empty' and len(signatures_set) > 100:
                signatures_for_comparison = process_barcodes(signatures_set)
                if len(signatures_for_comparison) == 0:
                    for entry in signatures_no_barcode_set:
                        signatures_for_comparison.add(entry + 'mixed:')

            else:
                signatures_for_comparison = signatures_set

        result['fastq_signature'] = sorted(list(signatures_for_comparison))
        check_for_fastq_signature_conflicts(
            session,
            url,
            errors,
            item,
            signatures_for_comparison)


def process_barcodes(signatures_set):
    set_to_return = set()
    flowcells_dict = {}
    for entry in signatures_set:
        (f, l, r, b, rest) = entry.split(':')
        if (f, l, r) not in flowcells_dict:
            flowcells_dict[(f, l, r)] = {}
        if b not in flowcells_dict[(f, l, r)]:
            flowcells_dict[(f, l, r)][b] = 0
        flowcells_dict[(f, l, r)][b] += 1
    for key in flowcells_dict.keys():
        barcodes_dict = flowcells_dict[key]
        total = 0
        for b in barcodes_dict.keys():
            total += barcodes_dict[b]
        for b in barcodes_dict.keys():
            if ((float(total)/float(barcodes_dict[b])) < 100):
                set_to_return.add(key[0] + ':' +
                                  key[1] + ':' +
                                  key[2] + ':' +
                                  b + ':')
    return set_to_return


def process_read_lengths(read_lengths_dict,
                         lengths_list,
                         submitted_read_length,
                         read_count,
                         threshold_percentage,
                         errors_to_report,
                         result):
    reads_quantity = sum([count for length, count in read_lengths_dict.items()
                          if (submitted_read_length - 2) <= length <= (submitted_read_length + 2)])
    informative_length_list = []
    for readLength in lengths_list:
        informative_length_list.append('bp, '.join(map(str,readLength)))
    if ((threshold_percentage * read_count) > reads_quantity):
        errors_to_report['read_length'] = \
            'in file metadata the read_length is {}bp, '.format(submitted_read_length) + \
            'however the uploaded fastq file contains reads of following length(s) ' + \
            '{}. '.format(', '.join(map(str, ['(%s)' % item for item in informative_length_list])))
        update_content_error(errors_to_report,
                             'Fastq file metadata specified read length was {}bp, '.format(
                                 submitted_read_length) +
                             'but the file contains read length(s) {}'.format(
                                ', '.join(map(str, ['(%s)' %item for item in informative_length_list]))))


def create_a_list_of_barcodes(details):
    barcodes = set()
    for entry in details:
        barcode = entry.get('barcode')
        lane = entry.get('lane')
        if lane and barcode:
            barcodes.add((lane, barcode))
    return barcodes


def compare_flowcell_details(flowcell_details_1, flowcell_details_2):
    barcodes_1 = create_a_list_of_barcodes(flowcell_details_1)
    barcodes_2 = create_a_list_of_barcodes(flowcell_details_1)
    if barcodes_1 & barcodes_2:
        # intersection found
        return True
    # no intersection
    return False


def get_mapped_run_type_bam(job, bam_data_stream):
    """ 
    obtain mapped run type from all bams by using samtools stats
    """
    errors = job['errors']
    result = job['result']
    numPairedReads = None
    for line in bam_data_stream.stdout.readlines():
        line = line.strip()
        try:
            assert('Failure' not in line)
        except AssertionError:
            errors['samtools_stats_decoding_failure'] = line
            update_content_error(errors, 'File failed samtools stats extraction. ' +
                                            errors['samtools_stats_decoding_failure'])
        else:
            if 'SN' in line and 'reads paired' in line.strip():
                line = line.split('\t')
                numPairedReads = line[2]
                result['samtools_stats_mapped_run_type_extraction'] = line
    runType = None
    if numPairedReads:
        if int(numPairedReads) > 0:
            runType = 'paired-ended'
        else:
            runType = 'single-ended'

    return runType


def  get_mapped_read_length_bam(job, bam_data_stream):
    """ 
    obtain mapped read length from all bams by using samtools stats
    """
    errors = job['errors']
    result = job['result']
    readLength = None
    for line in bam_data_stream.stdout.readlines():
        line = line.strip()
        try:
            assert('Failure' not in line)
        except AssertionError:
            errors['samtools_stats_decoding_failure'] = line
            update_content_error(errors, 'File failed samtools stats extraction. ' +
                                            errors['samtools_stats_decoding_failure'])
        else:
            line = line.split('\t')
            readLength = int(line[0])
            result['samtools_stats_mapped_read_length_extraction'] = line  
    return readLength


def get_read_name_details(job_id, errors, session, url):
    query = job_id +'?datastore=database&frame=object&format=json'
    try:
        r = session.get(urljoin(url, query))
    except requests.exceptions.RequestException as e:
        errors['lookup_for_read_name_detaisl'] = ('Network error occured, while looking for '
                                                  'file read_name details on the portal. {}').format(str(e))
    else:
        details = r.json().get('read_name_details')
        if details:
            return details


def get_platform_uuid(job_id, errors, session, url):
    query = job_id +'?datastore=database&frame=object&format=json'
    try:
        r = session.get(urljoin(url, query))
    except requests.exceptions.RequestException as e:
        errors['lookup_for_platform'] = ('Network error occured, while looking for '
                                         'platform on the portal. {}').format(str(e))
    else:
        platform_id = r.json().get('platform')
        if platform_id:
            query = platform_id +'?datastore=database&frame=object&format=json'
            try:
                r = session.get(urljoin(url, query))
            except requests.exceptions.RequestException as e:
                errors['lookup_for_platform'] = ('Network error occured, while looking for '
                                                'platform on the portal. {}').format(str(e))
            else:
                platform_uuid = r.json().get('uuid')
                return platform_uuid
        return platform_id  

def get_all_derived_from(job_id, errors, session, url):
    '''
    following logic from property_closure function from encoded
    '''
    derived_from_list = set()
    remaining = {str(job_id)}
    while remaining:
        derived_from_list.update(remaining)
        next_remaining = set()
        for file in remaining: 
            query = file +'?datastore=database&frame=object&format=json'
            try:
                r = session.get(urljoin(url, query))
            except requests.exceptions.RequestException as e:
                errors['lookup_for_file_derived_from'] = ('Network error occured, while looking for '
                                                'derived_from on the portal. {}').format(str(e))
            else:
                try:
                    next_remaining.update(r.json().get('derived_from'))
                except TypeError:
                    pass 
        remaining = next_remaining - derived_from_list
    return derived_from_list


def get_platform_from_bams(job_id, errors, session, url):
    query = job_id +'?datastore=database&frame=object&format=json'
    platform_list = []
    try:
        r = session.get(urljoin(url, query))
    except requests.exceptions.RequestException as e:
        errors['lookup_for_derived_from'] = ('Network error occured, while looking for '
                                         'derived_from on the portal. {}').format(str(e))
    else:
        derived_from_list = get_all_derived_from(r.json().get('@id'), errors, session, url)
        if derived_from_list:
            for file in derived_from_list:
                query = file +'?datastore=database&frame=object&format=json'
                try:
                    r = session.get(urljoin(url, query))
                except requests.exceptions.RequestException as e:
                    errors['lookup_for_file'] = ('Network error occured, while looking for '
                                                    'file_format on the portal. {}').format(str(e))
                else:
                    file_format = r.json().get('file_format')
                    if file_format == 'fastq':
                        platform_uuid = get_platform_uuid(file, errors, session, url)
                        if platform_uuid:
                            platform_list.append(platform_uuid)
    return set(platform_list)


def check_for_fastq_signature_conflicts(session,
                                        url,
                                        errors,
                                        item,
                                        signatures_to_check):
    conflicts = []
    for signature in sorted(list(signatures_to_check)):
        if not signature.endswith('mixed:'):
            query = '/search/?type=File&status!=replaced&file_format=fastq&' + \
                    'datastore=database&fastq_signature=' + signature
            try:
                r = session.get(urljoin(url, query))
            except requests.exceptions.RequestException as e:
                errors['lookup_for_fastq_signature'] = 'Network error occured, while looking for ' + \
                                                       'fastq signature conflict on the portal. ' + \
                                                       str(e)
            else:
                r_graph = r.json().get('@graph')
                # found a conflict
                if len(r_graph) > 0:
                    #  the conflict in case of missing barcode in read names could be resolved with metadata flowcell details
                    for entry in r_graph:
                        if (not signature.endswith('::') or
                            (signature.endswith('::') and entry.get('flowcell_details') and
                             item.get('flowcell_details') and
                             compare_flowcell_details(entry.get('flowcell_details'),
                                                      item.get('flowcell_details')))):
                                if 'accession' in entry and 'accession' in item and \
                                   entry['accession'] != item['accession']:
                                        conflicts.append(
                                            '%s in file %s ' % (
                                                signature,
                                                entry['accession']))
                                elif 'accession' in entry and 'accession' not in item:
                                    conflicts.append(
                                        '%s in file %s ' % (
                                            signature,
                                            entry['accession']))
                                elif 'accession' not in entry and 'accession' not in item:
                                    conflicts.append(
                                        '%s ' % (
                                            signature) +
                                        'file on the portal.')

    # "Fastq file contains read name signatures that conflict with signatures from file X”]
    if len(conflicts) > 0:
        errors['not_unique_flowcell_details'] = 'Fastq file contains read name signature ' + \
                                                'that conflict with signature of existing ' + \
                                                'file(s): {}'.format(
                                                ', '.join(map(str, conflicts)))
        update_content_error(errors, 'Fastq file contains read name signature ' +
                                     'that conflict with signature of existing ' +
                                     'file(s): {}'.format(
                                         ', '.join(map(str, conflicts))))
        return False
    return True


def check_for_contentmd5sum_conflicts(item, result, output, errors, session, url):
    result['content_md5sum'] = output[:32].decode(errors='replace')
    try:
        int(result['content_md5sum'], 16)
    except ValueError:
        errors['content_md5sum'] = output.decode(errors='replace').rstrip('\n')
        update_content_error(errors, 'File content md5sum format error')
    else:
        query = '/search/?type=File&status!=replaced&datastore=database&content_md5sum=' + result[
            'content_md5sum']
        try:
            r = session.get(urljoin(url, query))
        except requests.exceptions.RequestException as e:
            errors['lookup_for_content_md5sum'] = 'Network error occured, while looking for ' + \
                                                  'content md5sum conflict on the portal. ' + str(e)
        else:
            try:
                r_graph = r.json().get('@graph')
            except ValueError:
                errors['content_md5sum_lookup_json_error'] = str(r)
            else:
                if len(r_graph) > 0:
                    conflicts = []
                    for entry in r_graph:
                        if 'accession' in entry and 'accession' in item:
                            if entry['accession'] != item['accession']:
                                conflicts.append(
                                    '%s in file %s ' % (
                                        result['content_md5sum'],
                                        entry['accession']))
                        elif 'accession' in entry:
                            conflicts.append(
                                '%s in file %s ' % (
                                    result['content_md5sum'],
                                    entry['accession']))
                        elif 'accession' not in entry and 'accession' not in item:
                            conflicts.append(
                                '%s ' % (
                                    result['content_md5sum']))
                    if len(conflicts) > 0:
                        errors['content_md5sum'] = str(conflicts)
                        update_content_error(errors,
                                             'File content md5sum conflicts with content ' +
                                             'md5sum of existing file(s) {}'.format(
                                                 ', '.join(map(str, conflicts))))


def check_file(config, session, url, job):
    item = job['item']
    errors = job['errors']
    result = job['result'] = {}

    if job.get('skip'):
        return job

    local_path = job.get('local_file')
    if not local_path:
        no_file_flag = item.get('no_file_available')
        if no_file_flag:
            return job
        else:
            download_url = job['download_url']
            local_path = os.path.join(config['mirror'], download_url[len('s3://'):])
    # boolean standing for local .bed file creation
    is_local_bed_present = False
    if item['file_format'] == 'bed':
        # local_path[-18:-7] retreives the file accession from the path
        unzipped_modified_bed_path = local_path[-18:-7] + '_modified.bed'
    try:
        file_stat = os.stat(local_path)
    #  When file is not on S3 we are getting FileNotFoundError
    except FileNotFoundError:
        if job['run'] > job['upload_expiration']:
            errors['file_not_found'] = 'File has not been uploaded yet.'
        else:
            errors['file_not_found_unexpired_credentials'] = (
                'File has not been uploaded yet, but the credentials are not expired, '
                'so the status was not changed.'
            )
        job['skip'] = True
        return job
    #  Happens when there is S3 connectivity issue: "OSError: [Errno 107] Transport endpoint is not connected"
    except OSError:
        errors['file_check_skipped_due_to_s3_connectivity'] = (
            'File check was skipped due to temporary S3 connectivity issues'
        )
        job['skip'] = True
        return job
    else:
        result["file_size"] = file_stat.st_size
        result["last_modified"] = datetime.datetime.utcfromtimestamp(
            file_stat.st_mtime).isoformat() + 'Z'

        # Faster than doing it in Python.
        try:
            output = subprocess.check_output(
                ['md5sum', local_path], stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            errors['md5sum'] = e.output.decode(errors='replace').rstrip('\n')
        else:
            result['md5sum'] = output[:32].decode(errors='replace')
            try:
                int(result['md5sum'], 16)
            except ValueError:
                errors['md5sum'] = output.decode(errors='replace').rstrip('\n')
            if result['md5sum'] != item['md5sum']:
                errors['md5sum'] = \
                    'checked %s does not match item %s' % (result['md5sum'], item['md5sum'])
                update_content_error(errors,
                                     'File metadata-specified md5sum {} '.format(item['md5sum']) +
                                     'does not match the calculated md5sum {}'.format(result['md5sum']))
        try:
            is_gzipped = is_path_gzipped(local_path)
        except Exception as e:
            return job
        else:
            if item['file_format'] not in GZIP_TYPES:
                if is_gzipped:
                    errors['gzip'] = 'Expected un-gzipped file'
                    update_content_error(errors, 'Expected un-gzipped file')
            elif not is_gzipped:
                errors['gzip'] = 'Expected gzipped file'
                update_content_error(errors, 'Expected gzipped file')
            else:
                # May want to replace this with something like:
                # $ cat $local_path | tee >(md5sum >&2) | gunzip | md5sum
                # or http://stackoverflow.com/a/15343686/199100
                try:
                    output = subprocess.check_output(
                        'set -o pipefail; gunzip --stdout %s | md5sum' % quote(local_path),
                        shell=True, executable='/bin/bash', stderr=subprocess.STDOUT)
                except subprocess.CalledProcessError as e:
                    errors['content_md5sum'] = e.output.decode(errors='replace').rstrip('\n')
                else:
                    check_for_contentmd5sum_conflicts(item, result, output, errors, session, url)

                if item['file_format'] == 'bed':
                    # try to count comment lines
                    try:
                        output = subprocess.check_output(
                            'set -o pipefail; gunzip --stdout {} | grep -c \'^#\''.format(local_path),
                            shell=True, executable='/bin/bash', stderr=subprocess.STDOUT)
                    except subprocess.CalledProcessError as e:
                        # empty file, or other type of error
                        if e.returncode > 1:
                            errors['grep_bed_problem'] = e.output.decode(errors='replace').rstrip('\n')
                    # comments lines found, need to calculate content md5sum as usual
                    # remove the comments and create modified.bed to give validateFiles scritp
                    # not forget to remove the modified.bed after finishing
                    else:
                        try:
                            is_local_bed_present = True
                            subprocess.check_output(
                                'set -o pipefail; gunzip --stdout {} | grep -v \'^#\' > {}'.format(
                                    local_path,
                                    unzipped_modified_bed_path),
                                shell=True, executable='/bin/bash', stderr=subprocess.STDOUT)
                        except subprocess.CalledProcessError as e:
                            # empty file
                            if e.returncode > 1:
                                errors['grep_bed_problem'] = e.output.decode(errors='replace').rstrip('\n')
                            else:
                                errors['bed_comments_remove_failure'] = e.output.decode(
                                    errors='replace').rstrip('\n')

            if is_local_bed_present:
                check_format(config['encValData'], job, unzipped_modified_bed_path)
                remove_local_file(unzipped_modified_bed_path, errors)
            else:
                check_format(config['encValData'], job, local_path)

            if item['file_format'] == 'fastq' and not errors.get('validateFiles'):
                try:
                    process_fastq_file(job,
                                    subprocess.Popen(['gunzip --stdout {}'.format(
                                                        local_path)],
                                                        shell=True,
                                                        executable='/bin/bash',
                                                        stdout=subprocess.PIPE),
                                    session, url)
                except subprocess.CalledProcessError as e:
                    errors['fastq_information_extraction'] = 'Failed to extract information from ' + \
                                                            local_path
            if item['file_format'] == 'tsv' and item['output_type'] == 'guide quantifications':
                try:
                    if item['file_format_type'] == 'guide quantifications' and item['assembly'] == 'GRCh38':
                        validate_crispr(job, local_path)
                except KeyError:
                    pass
            if item['file_format'] == 'bam' and not errors.get('validateFiles') and 'subreads' not in item['output_type']:
                platform_list = get_platform_from_bams(job.get('@id'), errors, session, url)
                if platform_list:
                    not_Nanopore_PacBio_Ultima = True
                    for platform in platform_list:
                        if platform in ['ced61406-dcc6-43c4-bddd-4c977cc676e8',
                                        'c7564b38-ab4f-4c42-a401-3de48689a998',
                                        'e2be5728-5744-4da4-8881-cb9526d0389e',
                                        '7cc06b8c-5535-4a77-b719-4c23644e767d',
                                        '8f1a9a8c-3392-4032-92a8-5d196c9d7810',
                                        '6c275b37-018d-4bf8-85f6-6e3b830524a9',
                                        '6ce511d5-eeb3-41fc-bea7-8c38301e88c1',
                                        '25acccbd-cb36-463b-ac96-adbac11227e6'
                                        ]:
                            not_Nanopore_PacBio_Ultima = False
                            break
                    if not_Nanopore_PacBio_Ultima:
                        runType = None
                        readLength = None
                        try:
                            runType = get_mapped_run_type_bam(job,subprocess.Popen(
                                ['samtools', 'stats', local_path], 
                                                            stdout=subprocess.PIPE,
                                                            stderr=subprocess.PIPE, 
                                                            universal_newlines=True))
                            # command from samtools documentation: http://www.htslib.org/doc/samtools-stats.html 
                            readLength = get_mapped_read_length_bam(job,subprocess.Popen(
                                ['samtools stats {} | grep ^RL | cut -f 2- | sort -k2 -n -r | head -1'.format(
                                                            local_path)], 
                                                            stdout=subprocess.PIPE,
                                                            stderr=subprocess.PIPE,
                                                            shell=True,
                                                            executable='/bin/bash',
                                                            universal_newlines=True))
                        except subprocess.CalledProcessError as e:
                            errors['samtools_stats_extraction'] = 'Failed to extract information from ' + \
                                                                    local_path
                            update_content_error(errors, 'File failed samtools stats extraction ' +
                                            errors['samtools_stats_extraction'])
                        else:
                            result['samtools_stats_extraction'] = 'Failed to extract information from ' + \
                                                                    local_path
                        if runType and readLength:
                                result['mapped_run_type'] = runType
                                result['mapped_read_length'] = readLength
                        else:
                            errors['missing_mapped_properties'] = 'Failed to extract mapped read length and/or mapped run type from ' + \
                                                                    local_path
                            update_content_error(errors, 'File failed samtools stats extraction. ' +
                                            errors['missing_mapped_properties'])
        if item['status'] != 'uploading':
            errors['status_check'] = \
                "status '{}' is not 'uploading'".format(item['status'])
        if errors:
            errors['gathered information'] = 'Gathered information about the file was: {}.'.format(
                str(result))

        return job


def remove_local_file(path_to_the_file, errors):
    try:
        path_to_the_file = path_to_the_file
        if os.path.exists(path_to_the_file):
            try:
                os.remove(path_to_the_file)
            except OSError:
                errors['file_remove_error'] = 'OS could not remove the file ' + \
                                              path_to_the_file
    except NameError:
        pass

def extract_accession(file_path):
    return file_path.split('/')[-1].split('.')[0]

def fetch_files(session, url, search_query, out, include_unexpired_upload=False, file_list=None, local_file=None):
    graph = []
    # checkfiles using a file with a list of file accessions to be checked
    if file_list:
        r = None
        ACCESSIONS = []
        if os.path.isfile(file_list):
            ACCESSIONS = [line.rstrip('\n') for line in open(file_list)]
        for acc in ACCESSIONS:
            r = session.get(
                urljoin(url, '/search/?field=@id&limit=all&type=File&accession=' + acc))
            try:
                r.raise_for_status()
            except requests.HTTPError:
                return
            else:
                local = copy.deepcopy(r.json()['@graph'])
                graph.extend(local)
    # checkfiles using a query
    elif local_file:
        r = session.get(
            urljoin(url, '/search/?field=@id&limit=all&type=File&accession=' + extract_accession(local_file)))
        try:
            r.raise_for_status()
        except requests.HTTPError:
            return
        else:
            graph = r.json()['@graph']
    else:
        r = session.get(
            urljoin(url, '/search/?field=@id&limit=all&type=File&' + search_query))
        try:
            r.raise_for_status()
        except requests.HTTPError:
            return
        else:
            graph = r.json()['@graph']

    for result in graph:
        job = {
            '@id': result['@id'],
            'errors': {},
            'run': datetime.datetime.utcnow().isoformat() + 'Z',
        }
        errors = job['errors']
        item_url = urljoin(url, job['@id'])
        fileObject = session.get(item_url)
        r = session.get(item_url + '@@upload?datastore=database')
        if not fileObject.ok:
            errors['file_HTTPError'] = ('HTTP error: unable to get file object')
        if fileObject.ok and r.ok:
            upload_credentials = r.json()['@graph'][0]['upload_credentials']
            try: 
                if fileObject.json()['s3_uri']:
                    job['download_url'] = fileObject.json()['s3_uri']
            except KeyError:
                try:
                    job['download_url'] = upload_credentials['upload_url']
                except KeyError:
                    errors['download_url_missing'] = ('download url is missing')
            # Files grandfathered from EDW have no upload expiration.
            job['upload_expiration'] = upload_credentials.get('expiration', '')
            # Only check files that will not be changed during the check.
            if job['run'] < job['upload_expiration']:
                if not include_unexpired_upload:
                    job['errors']['unexpired_credentials'] = (
                        'File status have not been changed, the file '
                        'check was skipped due to file\'s '
                        'unexpired upload credentials'
                    )
        else:
            job['errors']['get_upload_url_request'] = \
                '{} {}\n{}'.format(r.status_code, r.reason, r.text)
        r = session.get(item_url + '?frame=edit&datastore=database')
        if r.ok:
            item = job['item'] = r.json()
            job['etag'] = r.headers['etag']
        else:
            errors['get_edit_request'] = \
                '{} {}\n{}'.format(r.status_code, r.reason, r.text)

        if errors:
            # Probably a transient error
            job['skip'] = True

        if local_file:
            job['local_file'] = local_file

        yield job


def patch_file(session, url, job):
    result = job['result']
    errors = job['errors']
    data = {}

    if not errors and not job.get('skip'):
        data = {
            'status': 'in progress'
        }
    else:
        if 'fastq_format_readname' in errors:
            update_content_error(errors,
                                 'Fastq file contains read names that don’t follow ' +
                                 'the Illumina standard naming schema; for example {}'.format(
                                     errors['fastq_format_readname']))
        # content_error_detail is truncated to allow indexing in cases of very long error messages
        if 'content_error' in errors:
            data = {
                'status': 'content error',
                'content_error_detail': errors['content_error'][:5000].strip()
                }
        if 'file_not_found' in errors:
            data = {
                'status': 'upload failed'
                }
    if 'file_size' in result:
        data['file_size'] = result['file_size']
    if 'read_count' in result:
        data['read_count'] = result['read_count']
    if result.get('fastq_signature'):
        data['fastq_signature'] = result['fastq_signature']
    if 'content_md5sum' in result:
        data['content_md5sum'] = result['content_md5sum']
    if 'mapped_run_type' in result:
        data['mapped_run_type'] = result['mapped_run_type']
    if 'mapped_read_length' in result:
        data['mapped_read_length'] = result['mapped_read_length']

    if data:
        item_url = urljoin(url, job['@id'])

        try:
            etag_r = session.get(item_url + '?frame=edit&datastore=database')
        except requests.exceptions.RequestException as e:
            errors['lookup_for_etag'] = 'Network error occured, while looking for ' + \
                                                   'etag of the file object to be patched on the portal. ' + \
                                                   str(e)
        else:
            if etag_r.ok:
                if job['etag'] == etag_r.headers['etag']:
                    r = session.patch(
                        item_url,
                        data=json.dumps(data),
                        headers={
                            'If-Match': job['etag'],
                            'Content-Type': 'application/json',
                        },
                    )
                    if not r.ok:
                        errors['patch_file_request'] = \
                            '{} {}\n{}'.format(r.status_code, r.reason, r.text)
                    else:
                        job['patched'] = True
                else:
                    errors['etag_does_not_match'] = 'Original etag was {}, but the current etag is {}.'.format(
                        job['etag'], etag_r.headers['etag']) + ' File {} '.format(job['item'].get('accession', 'UNKNOWN')) + \
                        'was {} and now is {}.'.format(job['item'].get('status', 'UNKNOWN'), etag_r.json()['status'])
    return


def run(out, err, url, username, password, encValData, mirror, search_query, file_list=None,
        bot_token=None, local_file=None, processes=None, include_unexpired_upload=False,
        dry_run=False, json_out=False):
    import functools
    import multiprocessing

    session = requests.Session()
    session.auth = (username, password)
    session.headers['Accept'] = 'application/json'

    config = {
        'encValData': encValData,
        'mirror': mirror,
    }

    dr = ""
    if dry_run:
        dr = "-- Dry Run"
    try:
        nprocesses = multiprocessing.cpu_count()
    except multiprocessing.NotImplmentedError:
        nprocesses = 1

    version = '1.25'

    try:
        ip_output = subprocess.check_output(
            ['hostname'], stderr=subprocess.STDOUT).strip()
        ip = ip_output.decode(errors='replace').rstrip('\n')
    except subprocess.CalledProcessError as e:
        ip = ''

    initiating_run = 'STARTING Checkfiles version ' + \
        '{} ({}) ({}): with {} processes {} on {} at {}'.format(
            version, url, search_query, nprocesses, dr, ip, datetime.datetime.now())
    if bot_token:
        sc = SlackClient(bot_token)
        sc.api_call(
            "chat.postMessage",
            channel="#bot-reporting",
            text=initiating_run,
            as_user=True
        )

    out.write(initiating_run + '\n')
    out.flush()
    if processes == 0:
        # Easier debugging without multiprocessing.
        imap = map
    else:
        pool = multiprocessing.Pool(processes=processes)
        imap = pool.imap_unordered

    jobs = fetch_files(session, url, search_query, out, include_unexpired_upload, file_list, local_file)
    if not json_out:
        headers = '\t'.join(['Accession', 'Lab', 'Errors', 'Aliases', 'Download URL',
                                'Upload Expiration'])
        out.write(headers + '\n')
        out.flush()
    for job in imap(functools.partial(check_file, config, session, url), jobs):
        if not dry_run:
            patch_file(session, url, job)
        tab_report = '\t'.join([
            job['item'].get('accession', 'UNKNOWN'),
            job['item'].get('lab', 'UNKNOWN'),
            str(job['errors']),
            str(job['item'].get('aliases', ['n/a'])),
            job.get('download_url', ''),
            job.get('upload_expiration', ''),
            ])
        if json_out:
            out.write(json.dumps(job) + '\n')
            out.flush()
            if job['errors']:
                err.write(json.dumps(job) + '\n')
                err.flush()
        else:
            out.write(tab_report + '\n')
            out.flush()
            if job['errors']:
                err.write(tab_report + '\n')
                err.flush()

    finishing_run = 'FINISHED Checkfiles at {}'.format(datetime.datetime.now())
    out.write(finishing_run + '\n')
    out.flush()
    output_filename = out.name
    out.close()
    error_filename = err.name
    err.close()

    if bot_token:
        with open(output_filename, 'r') as output_file:
            x = sc.api_call("files.upload",
                            title=output_filename,
                            channels='#bot-reporting',
                            content=output_file.read(),
                            as_user=True)

        with open(error_filename, 'r') as output_file:
            x = sc.api_call("files.upload",
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
        description="Update file status", epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--mirror', default='/s3')
    parser.add_argument(
        '--encValData', default='/opt/encValData', help="encValData location")
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
        '--processes', type=int,
        help="defaults to cpu count, use 0 for debugging in a single process")
    parser.add_argument(
        '--include-unexpired-upload', action='store_true',
        help="include files whose upload credentials have not yet expired (may be replaced!)")
    parser.add_argument(
        '--dry-run', action='store_true', help="Don't update status, just check")
    parser.add_argument(
        '--json-out', action='store_true', help="Output results as JSON (legacy)")
    parser.add_argument(
        '--search-query', default='status=uploading',
        help="override the file search query, e.g. 'accession=ENCFF000ABC'")
    parser.add_argument(
        '--file-list', default='',
        help="list of file accessions to check")
    parser.add_argument(
        '--local-file', default='',
        help="path to local file to check")
    parser.add_argument('url', help="server to post to")
    args = parser.parse_args()
    run(**vars(args))


if __name__ == '__main__':
    main()
