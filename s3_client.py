"""S3 Client for interacting with an S3 bucket.

Reads environment variables for the S3 bucket name, endpoint, access key,
and secret key. Works with S3-compatible storage services such as Ceph RGW.
"""

import os
from pathlib import Path
from typing import Callable, Iterator, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError


class S3Client:
    """Generic S3 client for listing, downloading and uploading S3 objects."""

    def __init__(self, bucket_name: Optional[str] = None):
        self.bucket_name = bucket_name or os.environ.get("S3_BUCKET")
        if not self.bucket_name:
            raise ValueError(
                "S3 bucket name is not set. Provide bucket_name or set S3_BUCKET."
            )
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT"),
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            config=Config(
                signature_version="s3v4",
                max_pool_connections=64,
                s3={
                    "addressing_style": os.environ.get(
                        "S3_ADDRESSING_STYLE",
                        "path",
                    )
                },
            ),
        )

    def _transfer_config(self) -> TransferConfig:
        """Create transfer config optimized for larger files such as videos."""
        return TransferConfig(
            multipart_threshold=64 * 1024 * 1024,
            multipart_chunksize=64 * 1024 * 1024,
            max_concurrency=4,
            use_threads=True,
        )

    def check_connection(self) -> bool:
        """Check if the S3 bucket is accessible."""
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
            return True
        except ClientError:
            return False

    def get_bucket_name(self) -> str:
        """Get the S3 bucket name."""
        return self.bucket_name

    def list_folders(self, prefix: str = "") -> Iterator[str]:
        """List the folders directly under the given prefix."""
        paginator = self.s3_client.get_paginator("list_objects_v2")

        for page in paginator.paginate(
            Bucket=self.bucket_name,
            Prefix=prefix,
            Delimiter="/",
        ):
            for common_prefix in page.get("CommonPrefixes", []):
                yield common_prefix["Prefix"]

    def list_objects(self, prefix: str = "", recursive: bool = True) -> Iterator[str]:
        """List object keys in the S3 bucket with the given prefix."""
        paginator = self.s3_client.get_paginator("list_objects_v2")

        params = {
            "Bucket": self.bucket_name,
            "Prefix": prefix,
        }

        if not recursive:
            params["Delimiter"] = "/"

        for page in paginator.paginate(**params):
            for obj in page.get("Contents", []):
                key = obj["Key"]

                if not key.endswith("/"):
                    yield key

    def list_objects_info(
        self,
        prefix: str = "",
        recursive: bool = True,
    ) -> Iterator[dict]:
        """List objects with metadata such as key, size and last modified date."""
        paginator = self.s3_client.get_paginator("list_objects_v2")

        params = {
            "Bucket": self.bucket_name,
            "Prefix": prefix,
        }

        if not recursive:
            params["Delimiter"] = "/"

        for page in paginator.paginate(**params):
            for obj in page.get("Contents", []):
                key = obj["Key"]

                if key.endswith("/"):
                    continue

                yield {
                    "key": key,
                    "size": obj["Size"],
                    "last_modified": obj["LastModified"],
                }

    def check_object_exists(self, key: str) -> bool:
        """Check if an object exists in the S3 bucket."""
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
            return True
        except ClientError:
            return False

    def download_file(
        self,
        key: str,
        local_path: Path,
        progress: Optional[Callable[[int], None]] = None,
        skip_existing: bool = True,
        expected_size: Optional[int] = None,
    ) -> Path:
        """Download a file from S3 to a local path.

        If skip_existing is True and the local file already exists with the
        expected size, the download is skipped.
        """
        local_path = Path(local_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)

        if skip_existing and local_path.exists():
            if expected_size is None or local_path.stat().st_size == expected_size:
                return local_path

        temp_path = local_path.with_name(local_path.name + ".part")

        if temp_path.exists():
            temp_path.unlink()

        self.s3_client.download_file(
            Bucket=self.bucket_name,
            Key=key,
            Filename=str(temp_path),
            Config=self._transfer_config(),
            Callback=progress,
        )

        temp_path.replace(local_path)

        return local_path

    def upload_file(
        self,
        local_path: Path,
        key: str,
        progress: Optional[Callable[[int], None]] = None,
    ) -> None:
        """Upload a file from a local path to S3."""
        self.s3_client.upload_file(
            Filename=str(local_path),
            Bucket=self.bucket_name,
            Key=key,
            Config=self._transfer_config(),
            Callback=progress,
        )

    def download_prefix(
        self,
        prefix: str,
        local_dir: Path,
        strip_prefix: Optional[str] = None,
        skip_existing: bool = True,
    ) -> dict:
        """Download all objects under a prefix.

        Example:
            prefix = "cuts/T001/"
            strip_prefix = "cuts/"
            local_dir = Path("/root/dogtrace/gezelschapshonden")

        S3 key:
            cuts/T001/video.mp4

        Local path:
            /root/dogtrace/gezelschapshonden/T001/video.mp4
        """
        local_dir = Path(local_dir)
        objects = list(self.list_objects_info(prefix=prefix))

        if not objects:
            return {
                "prefix": prefix,
                "status": "no_files",
                "downloaded": 0,
                "cached": 0,
                "failed": 0,
                "errors": [],
            }

        downloaded = 0
        cached = 0
        failed = 0
        errors = []

        for obj in objects:
            key = obj["key"]
            size = obj["size"]

            if strip_prefix is not None:
                relative_path = Path(key).relative_to(strip_prefix)
            else:
                relative_path = Path(key).relative_to(prefix)

            local_path = local_dir / relative_path
            already_exists = local_path.exists() and local_path.stat().st_size == size

            try:
                self.download_file(
                    key=key,
                    local_path=local_path,
                    skip_existing=skip_existing,
                    expected_size=size,
                )

                if skip_existing and already_exists:
                    cached += 1
                else:
                    downloaded += 1

            except Exception as e:
                failed += 1
                errors.append(f"{key}: {e}")

        status = "ok" if failed == 0 else "partial"

        return {
            "prefix": prefix,
            "status": status,
            "downloaded": downloaded,
            "cached": cached,
            "failed": failed,
            "errors": errors,
        }

    def download_prefixes_parallel(
        self,
        prefixes: list[str],
        local_dir: Path,
        strip_prefix: Optional[str] = None,
        max_workers: int = 6,
        skip_existing: bool = True,
        verbose: bool = False,
    ) -> dict:
        """Download multiple prefixes in parallel.

        This is generic and can be reused for notebooks, scripts or a UI.
        """
        results = []

        def worker(prefix: str) -> dict:
            # Create a separate client per worker for safer threaded use.
            worker_client = S3Client(bucket_name=self.bucket_name)

            return worker_client.download_prefix(
                prefix=prefix,
                local_dir=local_dir,
                strip_prefix=strip_prefix,
                skip_existing=skip_existing,
            )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(worker, prefix): prefix for prefix in prefixes}

            for future in as_completed(futures):
                prefix = futures[future]

                try:
                    result = future.result()
                except Exception as e:
                    result = {
                        "prefix": prefix,
                        "status": "failed",
                        "downloaded": 0,
                        "cached": 0,
                        "failed": 1,
                        "errors": [str(e)],
                    }

                results.append(result)

                if verbose:
                    print(
                        f"{result['prefix']} | "
                        f"status={result['status']} | "
                        f"downloaded={result['downloaded']} | "
                        f"cached={result['cached']} | "
                        f"failed={result['failed']}"
                    )

        successful = [result for result in results if result["status"] == "ok"]

        not_downloaded = [result for result in results if result["status"] != "ok"]

        return {
            "total_prefixes": len(prefixes),
            "successful_prefixes": len(successful),
            "not_downloaded_prefixes": len(not_downloaded),
            "downloaded_files": sum(result["downloaded"] for result in results),
            "cached_files": sum(result["cached"] for result in results),
            "failed_files": sum(result["failed"] for result in results),
            "results": results,
            "not_downloaded": not_downloaded,
        }
