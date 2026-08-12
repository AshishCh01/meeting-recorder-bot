import { S3Client } from '@aws-sdk/client-s3';
import { Upload } from '@aws-sdk/lib-storage';
import fs from 'fs';

const s3 = new S3Client({
  endpoint: process.env.S3_ENDPOINT, // e.g. https://<project-ref>.storage.supabase.co/storage/v1/s3
  region: process.env.S3_REGION || 'us-east-1',
  credentials: {
    accessKeyId: process.env.S3_ACCESS_KEY_ID,
    secretAccessKey: process.env.S3_SECRET_ACCESS_KEY,
  },
  forcePathStyle: true, // required for Supabase's S3-compatible endpoint
});

export class SupabaseUploader {
  static async upload(localFilePath, storageKey) {
    const fileStream = fs.createReadStream(localFilePath);

    const upload = new Upload({
      client: s3,
      params: {
        Bucket: process.env.S3_BUCKET_NAME,
        Key: storageKey,
        Body: fileStream,
        ContentType: 'video/mp4',
      },
    });

    await upload.done();
    return storageKey; // backend generates the signed URL itself from this path
  }
}
