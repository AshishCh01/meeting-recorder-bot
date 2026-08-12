import 'dotenv/config';
import app from './api/server.js';

const PORT = process.env.PORT || 3000;

app.listen(PORT, () => {
  console.log(`meeting-bot listening on port ${PORT}`);
});
