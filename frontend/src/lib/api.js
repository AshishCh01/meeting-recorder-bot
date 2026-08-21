import axios from 'axios';
import { supabase } from './supabase';

const apiUrl = import.meta.env.VITE_API_URL;
if (!apiUrl) {
  // Without this, axios silently falls back to baseURL: undefined and
  // resolves every request against the frontend's own origin instead of
  // the backend - no error anywhere, just requests going to the wrong
  // place. VITE_* vars are baked in at build time (see frontend/Dockerfile),
  // so this must be caught here, not assumed to fail loudly elsewhere.
  throw new Error(
    'VITE_API_URL is not set. Set it as a build-time env var before building the frontend.'
  );
}

const api = axios.create({
  baseURL: apiUrl,
  timeout: 120000,
});

api.interceptors.request.use(async (config) => {
  const { data: { session } } = await supabase.auth.getSession();
  if (session?.access_token) {
    config.headers.Authorization = `Bearer ${session.access_token}`;
  }
  return config;
}, (error) => {
  return Promise.reject(error);
});

export default api;
