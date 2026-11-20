import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';

export default function Dashboard() {
  const navigate = useNavigate();
  const user = JSON.parse(localStorage.getItem('user') || '{}');
  
  const [repoUrl, setRepoUrl] = useState('');
  const [baseRef, setBaseRef] = useState('');
  const [targetRef, setTargetRef] = useState('');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  
  // Recent Projects এর জন্য স্টেট
  const [projects, setProjects] = useState([]);

  // পেজ লোড হলে প্রজেক্ট লিস্ট নিয়ে আসা
  useEffect(() => {
    fetchProjects();
  }, []);

  const fetchProjects = async () => {
    try {
      const token = localStorage.getItem('token');
      const res = await axios.get('http://localhost:8000/api/projects', {
        headers: { Authorization: `Bearer ${token}` }
      });
      setProjects(res.data);
    } catch (err) {
      console.error("Failed to fetch projects", err);
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    localStorage.removeItem('user');
    navigate('/login');
  };

  const handleAnalyze = async (e) => {
    e.preventDefault();
    setLoading(true);
    setMessage('');
    
    try {
      const token = localStorage.getItem('token');
      const payload = {
        repo_url: repoUrl.trim(),
        base_ref: baseRef.trim(),
        target_ref: targetRef.trim(),
        name: repoUrl.trim().split('/').pop() || "Release", 
        send_diff_to_llm: true
      };

      await axios.post('http://localhost:8000/api/projects', payload, {
        headers: { 
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json'
        }
      });
      
      setMessage('✅ Analysis started successfully! Check below in Recent Projects.');
      setRepoUrl('');
      setBaseRef('');
      setTargetRef('');
      fetchProjects(); // নতুন প্রজেক্ট লিস্ট আপডেট করা
    } catch (err) {
      setMessage(`❌ ${err.response?.data?.detail || 'Failed to start analysis.'}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-900 text-white">
      {/* Navbar */}
      <nav className="bg-slate-800 border-b border-slate-700 p-4 flex justify-between items-center">
        <h1 className="text-xl font-bold text-blue-400">ReleaseLens AI</h1>
        <div className="flex items-center gap-4">
          <span className="text-sm text-slate-300">Hello, {user.name || 'User'}</span>
          <button onClick={handleLogout} className="bg-red-500/20 text-red-400 hover:bg-red-500/30 px-4 py-2 rounded transition-all text-sm">
            Logout
          </button>
        </div>
      </nav>

      <div className="p-8 max-w-6xl mx-auto grid grid-cols-1 lg:grid-cols-2 gap-8">
        
        {/* Left Side: Analyze Form */}
        <div>
          <div className="bg-slate-800 rounded-xl p-6 border border-slate-700 mb-6">
            <h2 className="text-2xl font-bold mb-2">Welcome to Dashboard</h2>
            <p className="text-slate-400">Analyze GitHub releases and generate AI notes.</p>
          </div>

          <div className="bg-slate-800 p-6 rounded-xl border border-slate-700">
            <h3 className="text-lg font-bold mb-4">Analyze New Release</h3>
            <form onSubmit={handleAnalyze} className="space-y-4">
              <div>
                <label className="block text-slate-300 text-sm mb-1">GitHub Repository URL</label>
                <input 
                  type="text" required placeholder="https://github.com/pallets/flask"
                  className="w-full bg-slate-700 text-white rounded px-3 py-2 outline-none focus:ring-2 focus:ring-blue-500"
                  value={repoUrl} onChange={(e) => setRepoUrl(e.target.value)}
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-slate-300 text-sm mb-1">Base (Old)</label>
                  <input 
                    type="text" required placeholder="3.0.0"
                    className="w-full bg-slate-700 text-white rounded px-3 py-2 outline-none focus:ring-2 focus:ring-blue-500"
                    value={baseRef} onChange={(e) => setBaseRef(e.target.value)}
                  />
                </div>
                <div>
                  <label className="block text-slate-300 text-sm mb-1">Target (New)</label>
                  <input 
                    type="text" required placeholder="3.0.1"
                    className="w-full bg-slate-700 text-white rounded px-3 py-2 outline-none focus:ring-2 focus:ring-blue-500"
                    value={targetRef} onChange={(e) => setTargetRef(e.target.value)}
                  />
                </div>
              </div>
              <button type="submit" disabled={loading} className="w-full bg-blue-600 hover:bg-blue-500 text-white font-bold py-2 rounded transition-all disabled:opacity-50">
                {loading ? 'Analyzing...' : 'Start Analysis'}
              </button>
              {message && (
                <div className={`text-sm mt-4 p-3 rounded break-words ${message.includes('✅') ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'}`}>
                  {message}
                </div>
              )}
            </form>
          </div>
        </div>

        {/* Right Side: Recent Projects */}
        <div>
          <div className="bg-slate-800 p-6 rounded-xl border border-slate-700">
            <div className="flex justify-between items-center mb-4">
              <h3 className="text-lg font-bold">Recent Projects</h3>
              <button onClick={fetchProjects} className="text-sm text-blue-400 hover:underline">Refresh</button>
            </div>
            
            {projects.length === 0 ? (
              <p className="text-slate-500 text-sm">No projects analyzed yet.</p>
            ) : (
              <div className="space-y-3">
                {projects.map((p) => (
                  <div key={p.id} className="bg-slate-700/50 p-4 rounded-lg flex justify-between items-center border border-slate-600">
                    <div>
                      <h4 className="font-bold text-white">{p.name}</h4>
                      <p className="text-xs text-slate-400 mt-1">{p.base_ref} → {p.target_ref}</p>
                      <span className={`text-xs px-2 py-1 rounded mt-2 inline-block ${
                        p.status === 'done' ? 'bg-green-500/20 text-green-400' : 
                        p.status === 'failed' ? 'bg-red-500/20 text-red-400' : 
                        'bg-yellow-500/20 text-yellow-400'
                      }`}>
                        {p.status.toUpperCase()}
                      </span>
                    </div>
                    <button 
                      onClick={() => navigate(`/project/${p.id}`)}
                      className="bg-slate-600 hover:bg-slate-500 px-4 py-2 rounded text-sm transition-all"
                    >
                      View
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

      </div>
    </div>
  );
}