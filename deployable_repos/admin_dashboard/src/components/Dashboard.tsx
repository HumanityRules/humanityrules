import { useState, useEffect } from 'react'

interface Stats {
  totalUsers: number
  activeUsers: number
  newUsersThisMonth: number
  totalSessions: number
  avgSessionDuration: string
  usersByRole: {
    Admin: number
    Editor: number
    Viewer: number
  }
  activityByDay: Array<{
    day: string
    sessions: number
  }>
}

function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/stats')
      .then(res => {
        if (!res.ok) throw new Error('Failed to fetch stats')
        return res.json()
      })
      .then(data => {
        setStats(data)
        setLoading(false)
      })
      .catch(err => {
        setError(err.message)
        setLoading(false)
      })
  }, [])

  if (loading) return <div className="loading">Loading dashboard...</div>
  if (error) return <div className="error">Error: {error}</div>
  if (!stats) return null

  const maxSessions = Math.max(...stats.activityByDay.map(d => d.sessions))
  const totalRoleUsers = stats.usersByRole.Admin + stats.usersByRole.Editor + stats.usersByRole.Viewer

  return (
    <div className="dashboard">
      <h2>Dashboard</h2>

      <div className="stats-grid">
        <div className="stat-card highlight">
          <h3>Total Users</h3>
          <div className="value">{stats.totalUsers}</div>
        </div>
        <div className="stat-card">
          <h3>Active Users</h3>
          <div className="value">{stats.activeUsers}</div>
        </div>
        <div className="stat-card">
          <h3>New This Month</h3>
          <div className="value">{stats.newUsersThisMonth}</div>
        </div>
        <div className="stat-card">
          <h3>Total Sessions</h3>
          <div className="value">{stats.totalSessions}</div>
        </div>
        <div className="stat-card">
          <h3>Avg Session</h3>
          <div className="value">{stats.avgSessionDuration}</div>
        </div>
      </div>

      <div className="charts-section">
        <div className="chart-card">
          <h3>Weekly Activity</h3>
          <div className="bar-chart">
            {stats.activityByDay.map(item => (
              <div key={item.day} className="bar-item">
                <div
                  className="bar"
                  style={{ height: `${(item.sessions / maxSessions) * 150}px` }}
                  title={`${item.sessions} sessions`}
                />
                <span className="bar-label">{item.day}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="chart-card">
          <h3>Users by Role</h3>
          <div className="role-list">
            {Object.entries(stats.usersByRole).map(([role, count]) => (
              <div key={role} className="role-item">
                <div className={`role-color ${role.toLowerCase()}`} />
                <div className="role-info">
                  <div className="role-name">{role}</div>
                  <div className="role-count">{count} users</div>
                  <div className="role-bar">
                    <div
                      className={`role-bar-fill ${role.toLowerCase()}`}
                      style={{ width: `${(count / totalRoleUsers) * 100}%` }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

export default Dashboard
