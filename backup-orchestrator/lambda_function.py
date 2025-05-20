import boto3
import os
import datetime
import json

def lambda_handler(event, context):
    # Initialize AWS clients
    ec2 = boto3.client('ec2')
    sns = boto3.client('sns')
    cloudwatch = boto3.client('cloudwatch')

    # Get backup policy from event
    backup_policy = event.get('backup_policy', 'Standard')

    # Get EC2 instances with the specified backup policy
    instances_response = ec2.describe_instances(
        Filters=[
            {
                'Name': 'tag:BackupPolicy',
                'Values': [backup_policy],
            },
            {
                'Name': 'instance-state-name',
                'Values': ['running']
            }
        ]
    )

    successful_backups = []
    failed_backups = []

    # Iterate through instances
    for reservation in instances_response['Reservations']:
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']

            # Get instance tags
            instance_tags = {tag['Key']: tag['Value'] for tag in instance.get('Tags', [])}

            # Get volumes attached to the instance
            volumes_response = ec2.describe_volumes(
                Filters=[
                    {
                        'Name': 'attachment.instance-id',
                        'Values': [instance_id]
                    }
                ]
            )

            # Create snapshots for each volume
            for volume in volumes_response['Volumes']:
                volume_id = volume['VolumeId']
                try:
                    # Create timestamp
                    timestamp = datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
                    # Create description
                    description = f"Smart-Vault-Backup-{instance_id}-{volume_id}-{timestamp}"

                    # Create snapshot
                    snapshot_response = ec2.create_snapshot(
                        VolumeId=volume_id,
                        Description=description
                    )
                    snapshot_id = snapshot_response['SnapshotId']

                    # Create tags for the snapshot
                    tags = [
                        {'Key': 'Name', 'Value': description},
                        {'Key': 'InstanceId', 'Value': instance_id},
                        {'Key': 'CreatedBy', 'Value': 'SmartVault'},
                        {'Key': 'BackupPolicy', 'Value': backup_policy},
                        {'Key': 'CreationDate', 'Value': timestamp}
                    ]

                    # Add retention period tag
                    retention_days = instance_tags.get('RetentionPeriod', '30')
                    delete_after = (datetime.datetime.now() + datetime.timedelta(days=int(retention_days))).strftime('%Y-%m-%d')
                    tags.append({'Key': 'DeleteAfter', 'Value': delete_after})

                    # Add instance tags to snapshot (excluding sensitive ones)
                    for key, value in instance_tags.items():
                        if key not in ['Name', 'CreatedBy', 'BackupPolicy', 'DeleteAfter']:
                            tags.append({'Key': key, 'Value': value})

                    # Apply tags to snapshot
                    ec2.create_tags(
                        Resources=[snapshot_id],
                        Tags=tags
                    )

                    successful_backups.append({
                        'InstanceId': instance_id,
                        'VolumeId': volume_id,
                        'SnapshotId': snapshot_id
                    })
                except Exception as e:
                    failed_backups.append({
                        'InstanceId': instance_id,
                        'VolumeId': volume_id,
                        'Error': str(e)
                    })

    # Calculate success metrics
    total_attempts = len(successful_backups) + len(failed_backups)
    success_rate = (len(successful_backups) / total_attempts * 100) if total_attempts else 0.0

    # Push metrics to CloudWatch
    cloudwatch.put_metric_data(
        Namespace='SmartVault',
        MetricData=[
            {
                'MetricName': 'SuccessfulBackups',
                'Value': len(successful_backups),
                'Unit': 'Count',
                'Dimensions': [
                    {
                        'Name': 'BackupPolicy',
                        'Value': backup_policy,
                    }
                ]
            },
            {
                'MetricName': 'FailedBackups',
                'Value': len(failed_backups),
                'Unit': 'Count',
                'Dimensions': [
                    {
                        'Name': 'BackupPolicy',
                        'Value': backup_policy,
                    }
                ]
            },
            {
                'MetricName': 'BackupSuccessRate',
                'Value': success_rate,
                'Unit': 'Percent',
                'Dimensions': [
                    {
                        'Name': 'BackupPolicy',
                        'Value': backup_policy,
                    }
                ]
            }
        ]
    )

    # Send SNS notification
    sns_message = {
        'BackupPolicy': backup_policy,
        'TimeStamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'SuccessfulBackups': successful_backups,
        'FailedBackups': failed_backups,
        'TotalSuccessful': len(successful_backups),
        'TotalFailed': len(failed_backups),
        'SuccessRate': f"{success_rate:.2f}%"
    }

    sns.publish(
        TopicArn=os.environ['SNS_TOPIC_ARN'],
        Subject=f"Smart Vault Backup Report - {backup_policy}",
        Message=json.dumps(sns_message, indent=2),
    )

    # Return the results
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': f"Backup process completed for policy: {backup_policy}",
            'successful': len(successful_backups),
            'failed': len(failed_backups),
            'successRate': f"{success_rate:.2f}%"
        })
    }
