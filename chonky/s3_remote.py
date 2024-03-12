import concurrent.futures
import boto3 # type: ignore

from chonky.base_remote import BaseRemote

MAX_WORKERS = 16

class S3Remote(BaseRemote):
    def pull(self, keys: list[str]):
        session = boto3.session.Session()
        client = session.client("s3")
        def fetch(key):
            client.download_file(
                Bucket=self.remote_host,
                Key=str(self.remote_root.joinpath(key)),
                Filename=str(self.local_root.joinpath(key)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as exe:
            exe.map(fetch, keys)
        

    def push(self, keys: list[str]):
        s3 = boto3.resource('s3')
        bucket = s3.Bucket(self.remote_host)
        for key in keys:
            remote_key = str(self.remote_root.joinpath(key))
            if not list(bucket.objects.filter(Prefix=remote_key)):
                local_path = self.local_root.joinpath(key)
                bucket.upload_file(local_path, remote_key)
