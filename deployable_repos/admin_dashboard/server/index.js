import express from 'express';
import cors from 'cors';
import path from 'path';
import { fileURLToPath } from 'url';
import dotenv from 'dotenv';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());

// Mock user data
const users = [
  { id: 1, name: 'Alice Johnson', email: 'alice@example.com', role: 'Admin', status: 'Active', lastLogin: '2024-01-10T09:30:00Z' },
  { id: 2, name: 'Bob Smith', email: 'bob@example.com', role: 'Editor', status: 'Active', lastLogin: '2024-01-09T14:22:00Z' },
  { id: 3, name: 'Carol White', email: 'carol@example.com', role: 'Viewer', status: 'Active', lastLogin: '2024-01-08T11:15:00Z' },
  { id: 4, name: 'David Brown', email: 'david@example.com', role: 'Editor', status: 'Inactive', lastLogin: '2024-01-01T08:00:00Z' },
  { id: 5, name: 'Eve Davis', email: 'eve@example.com', role: 'Admin', status: 'Active', lastLogin: '2024-01-10T16:45:00Z' },
  { id: 6, name: 'Frank Miller', email: 'frank@example.com', role: 'Viewer', status: 'Active', lastLogin: '2024-01-07T10:30:00Z' },
  { id: 7, name: 'Grace Lee', email: 'grace@example.com', role: 'Editor', status: 'Active', lastLogin: '2024-01-09T09:00:00Z' },
  { id: 8, name: 'Henry Wilson', email: 'henry@example.com', role: 'Viewer', status: 'Inactive', lastLogin: '2023-12-20T15:00:00Z' },
];

// Mock stats data
const stats = {
  totalUsers: 8,
  activeUsers: 6,
  newUsersThisMonth: 2,
  totalSessions: 156,
  avgSessionDuration: '12m 34s',
  usersByRole: {
    Admin: 2,
    Editor: 3,
    Viewer: 3,
  },
  activityByDay: [
    { day: 'Mon', sessions: 24 },
    { day: 'Tue', sessions: 31 },
    { day: 'Wed', sessions: 28 },
    { day: 'Thu', sessions: 35 },
    { day: 'Fri', sessions: 22 },
    { day: 'Sat', sessions: 8 },
    { day: 'Sun', sessions: 8 },
  ],
};

// API Routes
app.get('/api/users', (req, res) => {
  res.json(users);
});

app.get('/api/users/:id', (req, res) => {
  const userId = parseInt(req.params.id, 10);
  const user = users.find(u => u.id === userId);
  if (user) {
    res.json(user);
  } else {
    res.status(404).json({ error: 'User not found' });
  }
});

app.get('/api/stats', (req, res) => {
  res.json(stats);
});

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({ status: 'healthy', timestamp: new Date().toISOString() });
});

// Serve static files in production
if (process.env.NODE_ENV === 'production') {
  const distPath = path.join(__dirname, '..', 'dist');
  app.use(express.static(distPath));

  // Handle client-side routing
  app.get('*', (req, res) => {
    res.sendFile(path.join(distPath, 'index.html'));
  });
}

app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
  console.log(`Health check: http://localhost:${PORT}/health`);
  if (process.env.NODE_ENV === 'production') {
    console.log(`App available at: http://localhost:${PORT}`);
  } else {
    console.log(`API available at: http://localhost:${PORT}/api`);
  }
});
