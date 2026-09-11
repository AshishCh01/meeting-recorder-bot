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

// Waits between attempts: 4 attempts, at most 17s spent waiting. Sized for
// the failure that actually lost a recording - a transient DNS blip - not
// for riding out a real Supabase outage; that is what keeping the local file
// and POST /reupload are for.
const RETRY_DELAYS_MS = [2000, 5000, 10000];

function putObjectToSupabase(storageKey, body) {
  return supabase.storage
    .from(bucketName)
    .upload(storageKey, body, {
      contentType: 'audio/mp4', // .m4a is an mp4 audio container
      upsert: true,             // Overwrite if a file with this name already exists - what makes a retry safe
      duplex: 'half'
    });
}

// A 4xx from Storage (bad key, missing bucket, file too large) will fail the
// same way every time, and re-sending an 86MB body three more times to learn
// that helps no one. Network failures ("fetch failed") carry no status and
// are exactly the case retrying is for. 408/429 are 4xx but transient.
function isRetryable(error) {
  const status = error?.status;
  if (typeof status !== 'number') return true;
  if (status === 408 || status === 429) return true;
  return status < 400 || status >= 500;
}

export class SupabaseUploader {
  // `putObject`, `retryDelaysMs` and `sleep` exist so tests can fake the
  // network and the clock; production callers pass nothing.
  static async upload(localFilePath, storageKey, {
    putObject = putObjectToSupabase,
    retryDelaysMs = RETRY_DELAYS_MS,
    sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
  } = {}) {
    console.log(`[SupabaseUploader] Preparing to upload to bucket '${bucketName}'...`);
    const maxAttempts = retryDelaysMs.length + 1;

    for (let attempt = 1; ; attempt++) {
      // A fresh stream on every attempt. A stream consumed by a failed attempt
      // cannot be re-read: handing it to attempt 2 uploads zero bytes and
      // reports success, overwriting the object with an empty file.
      const fileStream = fs.createReadStream(localFilePath);
      try {
        const { data, error } = await putObject(storageKey, fileStream);
        if (error) {
          throw error;
        }

        console.log(`[SupabaseUploader] Upload successful! File saved as: ${data.path}`);
        return data.path;

      } catch (error) {
        if (attempt >= maxAttempts || !isRetryable(error)) {
          console.error(`[SupabaseUploader] Upload failed (attempt ${attempt}/${maxAttempts}, giving up):`, error.message);
          throw error;
        }
        const delayMs = retryDelaysMs[attempt - 1];
        console.error(`[SupabaseUploader] Upload attempt ${attempt}/${maxAttempts} failed: ${error.message} - retrying in ${delayMs}ms`);
        await sleep(delayMs);
      } finally {
        // Release the file handle whether or not the attempt read it all -
        // an abandoned stream keeps the file open, which on Windows is enough
        // to make the caller's later unlink fail.
        fileStream.destroy();
      }
    }
  }
}
