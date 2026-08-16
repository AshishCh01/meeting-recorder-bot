import { createClient } from '@supabase/supabase-js';
import fs from 'fs';
import dotenv from 'dotenv';

// Load variables from your .env file
dotenv.config();

const supabaseUrl = process.env.SUPABASE_URL;
const supabaseKey = process.env.SUPABASE_KEY;
const bucketName = process.env.SUPABASE_RECORDINGS_BUCKET || 'recordings';

// Initialize the Supabase client using your service_role key
const supabase = createClient(supabaseUrl, supabaseKey);

export class SupabaseUploader {
  static async upload(localFilePath, storageKey) {
    console.log(`[SupabaseUploader] Preparing to upload to bucket '${bucketName}'...`);

    try {
      // 1. Read the .m4a file from your local hard drive using a stream
      const fileStream = fs.createReadStream(localFilePath);

      // 2. Upload it to the Supabase Storage bucket
      const { data, error } = await supabase.storage
        .from(bucketName)
        .upload(storageKey, fileStream, {
          contentType: 'audio/mp4', // .m4a is an mp4 audio container
          upsert: true,             // Overwrite if a file with this name already exists
          duplex: 'half'
        });

      if (error) {
        throw error;
      }

      console.log(`[SupabaseUploader] Upload successful! File saved as: ${data.path}`);
      return data.path;

    } catch (error) {
      console.error('[SupabaseUploader] Upload failed:', error.message);
      throw error;
    }
  }
}