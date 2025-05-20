import boto3
import datetime
import json
import os

def lambda_handler(event, context):
    # Initialize AWS clients
    ec2 = boto3.client('ec2')
    sns = boto3.client('sns')
    cloudwatch = boto3.client('cloudwatch')

    # Get current date
    current_date = datetime.datetime.now().strftime('%Y-%m-%d')

    # Find snapshots to delete
    snapshots_response = ec2.describe_snapshots(
        Filters=[
            {
                'Name': 'tag:CreatedBy',
                'Values': ['SmartVault']
            },
            {
                'Name': 'tag-key',
                'Values': ['DeleteAfter']
            }
        ],
        OwnerIds=['self']
    )

    deleted_snapshots = []
    failed_deletions = []

    # Process each snapshot
    for snapshot in snapshots_response['Snapshots']:
        snapshot_id = snapshot['SnapshotId']
        tags = {tag['Key']: tag['Value'] for tag in snapshot.get('Tags', [])}

        # Check if the snapshot should be deleted
        if 'DeleteAfter' in tags and tags['DeleteAfter'] <= current_date:
            try:
                ec2.delete_snapshot(SnapshotId=snapshot_id)
                deleted_snapshots.append({
                    'SnapshotId': snapshot_id,
                    'DeleteAfter': tags['DeleteAfter'],
                    'Description': snapshot.get('Description', 'N/A'),
                    'BackupPolicy': tags.get('BackupPolicy', 'N/A')
                })
            except Exception as e:
                failed_deletions.append({
                    'SnapshotId': snapshot_id,
                    'DeleteAfter': tags['DeleteAfter'],
                    'Error': str(e)
                })

    # Push metrics to CloudWatch
    cloudwatch.put_metric_data(
        Namespace='SmartVault',
        MetricData=[
            {
                'MetricName': 'SnapshotsDeleted',
                'Value': len(deleted_snapshots),
                'Unit': 'Count',
            },
            {
                'MetricName': 'SnapshotDeletionFailures',
                'Value': len(failed_deletions),
                'Unit': 'Count',
            }
        ]
    )

    # Send SNS notification
    sns_message = {
        'TimeStamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'DeletedSnapshots': deleted_snapshots,
        'FailedDeletions': failed_deletions,
        'TotalDeleted': len(deleted_snapshots),
        'TotalFailed': len(failed_deletions)
    }

    sns.publish(
        TopicArn=os.environ['SNS_TOPIC_ARN'],
        Subject="Smart Vault Retention Report",
        Message=json.dumps(sns_message, indent=2)
    )

    # Return results
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': "Retention process completed",
            'deleted': len(deleted_snapshots),
            'failed': len(failed_deletions)
        })
    }
