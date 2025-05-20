import boto3
import datetime
import json
import os

def lambda_handler(event, context):
    # Initialize AWS clients
    ec2 = boto3.client('ec2')
    sns = boto3.client('sns')
    cloudwatch = boto3.client('cloudwatch')

    # Get instances with backup tags
    instances_response = ec2.describe_instances(
        Filters=[
            {
                'Name': 'tag-key',
                'Values': ['BackupPolicy']
            }
        ]
    )

    # Get snapshots created by SmartVault
    snapshots_response = ec2.describe_snapshots(
        Filters=[
            {
                'Name': 'tag:CreatedBy',
                'Values': ['SmartVault']
            }
        ],
        OwnerIds=['self']
    )

    # Organize snapshots by instance
    snapshots_by_instance = {}
    for snapshot in snapshots_response['Snapshots']:
        tags = {tag['Key']: tag['Value'] for tag in snapshot.get('Tags', [])}
        if 'InstanceId' in tags:
            instance_id = tags['InstanceId']
            if instance_id not in snapshots_by_instance:
                snapshots_by_instance[instance_id] = []
            snapshots_by_instance[instance_id].append({
                'SnapshotId': snapshot['SnapshotId'],
                'StartTime': snapshot['StartTime'].strftime('%Y-%m-%d %H:%M:%S'),
                'DeleteAfter': tags.get('DeleteAfter', 'N/A'),
                'BackupPolicy': tags.get('BackupPolicy', 'N/A')
            })

    # Analyze backup compliance
    compliance_report = []
    for reservation in instances_response['Reservations']:
        for instance in reservation['Instances']:
            instance_id = instance['InstanceId']
            instance_tags = {tag['Key']: tag['Value'] for tag in instance.get('Tags', [])}
            backup_policy = instance_tags.get('BackupPolicy', 'None')

            if backup_policy == 'Exempt':
                continue

            instance_snapshots = snapshots_by_instance.get(instance_id, [])
            if instance_snapshots:
                instance_snapshots.sort(key=lambda x: x['StartTime'], reverse=True)
                last_backup = datetime.datetime.strptime(instance_snapshots[0]['StartTime'], '%Y-%m-%d %H:%M:%S')
                days_since_last_backup = (datetime.datetime.now() - last_backup).days
            else:
                days_since_last_backup = float('inf')

            # Determine compliance
            compliance_status = 'Non-Compliant'
            if backup_policy == 'Critical' and days_since_last_backup <= 1:
                compliance_status = 'Compliant'
            elif backup_policy == 'Standard' and days_since_last_backup <= 7:
                compliance_status = 'Compliant'
            elif backup_policy == 'Archive' and days_since_last_backup <= 30:
                compliance_status = 'Compliant'

            compliance_report.append({
                'InstanceId': instance_id,
                'BackupPolicy': backup_policy,
                'DaysSinceLastBackup': days_since_last_backup if days_since_last_backup != float('inf') else 'No Backup Found',
                'SnapshotCount': len(instance_snapshots),
                'ComplianceStatus': compliance_status,
                'Environment': instance_tags.get('Environment', 'Unknown'),
                'Department': instance_tags.get('Department', 'Unknown')
            })

    # Generate compliance stats
    total_instances = len(compliance_report)
    compliant_instances = sum(1 for item in compliance_report if item['ComplianceStatus'] == 'Compliant')
    compliance_percentage = (compliant_instances / total_instances * 100) if total_instances > 0 else 0.0

    # Push overall metrics to CloudWatch
    cloudwatch.put_metric_data(
        Namespace='SmartVault',
        MetricData=[
            {
                'MetricName': 'CompliancePercentage',
                'Value': compliance_percentage,
                'Unit': 'Percent',
            },
            {
                'MetricName': 'NonCompliantInstances',
                'Value': total_instances - compliant_instances,
                'Unit': 'Count'
            }
        ]
    )

    # Push environment-wise metrics
    for environment in set(item['Environment'] for item in compliance_report):
        env_instances = [item for item in compliance_report if item['Environment'] == environment]
        env_total = len(env_instances)
        env_compliant = sum(1 for item in env_instances if item['ComplianceStatus'] == 'Compliant')
        env_compliance_rate = (env_compliant / env_total * 100) if env_total > 0 else 0.0

        cloudwatch.put_metric_data(
            Namespace='SmartVault',
            MetricData=[
                {
                    'MetricName': 'CompliancePercentage',
                    'Value': env_compliance_rate,
                    'Unit': 'Percent',
                    'Dimensions': [
                        {
                            'Name': 'Environment',
                            'Value': environment
                        }
                    ]
                }
            ]
        )

    # Send SNS notification
    sns_message = {
        'TimeStamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'ComplianceReport': compliance_report,
        'ComplianceStats': {
            'TotalInstances': total_instances,
            'CompliantInstances': compliant_instances,
            'NonCompliantInstances': total_instances - compliant_instances,
            'CompliancePercentage': f"{compliance_percentage:.2f}%"
        }
    }

    sns.publish(
        TopicArn=os.environ['SNS_TOPIC_ARN'],
        Subject="Smart Vault Compliance Report",
        Message=json.dumps(sns_message, indent=2)
    )

    # Return the results
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': "Compliance report generated",
            'totalInstances': total_instances,
            'compliantInstances': compliant_instances,
            'compliancePercentage': f"{compliance_percentage:.2f}%"
        })
    }
