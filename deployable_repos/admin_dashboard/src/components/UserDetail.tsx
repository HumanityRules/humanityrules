import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'

interface User {
  id: number
  name: string
  email: string
  role: string
  status: string
  lastLogin: string
}

function UserDetail() {
  const { id } = useParams<{ id: string }>()
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch(`/api/users/${id}`)
      .then(res => {
        if (!res.ok) throw new Error('User not found')
        return res.json()
      })
      .then(data => {
        setUser(data)
        setLoading(false)
      })
      .catch(err => {
        setError(err.message)
        setLoading(false)
      })
  }, [id])

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  if (loading) return <div className="loading">Loading user...</div>
  if (error) return <div className="error">Error: {error}</div>
  if (!user) return null

  return (
    <div className="user-detail-page">
      <Link to="/users" className="back-link">
        &larr; Back to Users
      </Link>

      <div className="user-card">
        <div className="user-header">
          <h2>{user.name}</h2>
          <p>{user.email}</p>
        </div>

        <div className="user-info">
          <div className="info-grid">
            <div className="info-item">
              <label>User ID</label>
              <span>{user.id}</span>
            </div>
            <div className="info-item">
              <label>Role</label>
              <span>{user.role}</span>
            </div>
            <div className="info-item">
              <label>Status</label>
              <span className={`status-badge ${user.status.toLowerCase()}`}>
                {user.status}
              </span>
            </div>
            <div className="info-item">
              <label>Last Login</label>
              <span>{formatDate(user.lastLogin)}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default UserDetail
