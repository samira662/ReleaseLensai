import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import axios from 'axios';

export default function ProjectDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  
  const [project, setProject] = useState(null);
  const [changes, setChanges] = useState([]);
  const [notes, setNotes] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const token = localStorage.getItem('token');
        const headers = { Authorization: `Bearer ${token}` };
        
        // প্রজেক্ট, চেঞ্জেস এবং নোটস একসাথে লোড করা
        const [projectRes, changesRes, notesRes] = await Promise.all([
          axios.get(`http://localhost:8000/api/projects/${id}`, { headers }),
          axios.get(`http://localhost:8000/api/projects/${id}/changes`, { headers }),
          axios.get(`http://localhost:8000/api/projects/${id}/notes`, { headers }).catch(() => ({ data: [] })) // নোটস না থাকলে খালি অ্যারে
        ]);
        
        setProject(projectRes.data);
        setChanges(changesRes.data);
        setNotes(notesRes.data);
      } catch (err) {
        console.error("Error fetching data:", err);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [id]);

  if (loading) return <div className="min-h-screen bg-slate-900 text-white flex justify-center items-center">Loading analysis data...</div>;
  if (!project) return <div className="min-h-screen bg-slate-900 text-white flex justify-center items-center">Project not found.</div>;

  return (
    <div className="min-h-screen bg-slate-900 text-white p-8">
      <div className="max-w-5xl mx-auto">
        <button onClick={() => navigate('/dashboard')} className="text-blue-400 hover:underline mb-6 inline-block">
          ← Back to Dashboard
        </button>
        
        {/* Project Info & Risk */}
        <div className="bg-slate-800 p-6 rounded-xl border border-slate-700 mb-6">
          <h1 className="text-2xl font-bold text-white mb-2">{project.name} ({project.repo_full_name})</h1>
          <p className="text-slate-400 mb-4">
            Comparing <span className="text-white font-mono">{project.base_ref}</span> to <span className="text-white font-mono">{project.target_ref}</span>
          </p>
          
          <div className="bg-slate-700/50 p-4 rounded-lg flex gap-8">
            <div>
              <h3 className="font-bold text-sm text-slate-300">Status</h3>
              <p className="text-lg font-semibold text-green-400">{project.status.toUpperCase()}</p>
            </div>
            <div>
              <h3 className="font-bold text-sm text-slate-300">Risk Level</h3>
              <p className={`text-lg font-semibold ${project.risk_level === 'High' ? 'text-red-400' : project.risk_level === 'Medium' ? 'text-yellow-400' : 'text-green-400'}`}>
                {project.risk_level || 'N/A'}
              </p>
            </div>
          </div>
        </div>

        {/* AI Generated Release Notes */}
        <div className="bg-slate-800 p-6 rounded-xl border border-slate-700 mb-6">
          <h3 className="font-bold text-xl mb-4 border-b border-slate-700 pb-2">AI Generated Release Notes</h3>
          
          {notes.length === 0 ? (
            <p className="text-slate-400 text-sm">No release notes generated yet.</p>
          ) : (
            <div className="space-y-4">
              {notes.map((note) => (
                <div key={note.id} className="bg-slate-700/30 p-4 rounded-lg border border-slate-600">
                  <div className="flex justify-between items-center mb-2">
                    <h4 className="font-bold text-blue-400 uppercase">{note.audience.replace('_', ' ')} View</h4>
                    {note.ai_generated && (
                      <span className="bg-purple-500/20 text-purple-400 px-2 py-1 rounded text-xs font-bold">
                        AI DRAFT
                      </span>
                    )}
                  </div>
                  <div className="text-sm text-slate-300 whitespace-pre-wrap font-mono bg-slate-900/50 p-4 rounded">
                    {note.content}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Changes List */}
        <div className="bg-slate-800 p-6 rounded-xl border border-slate-700">
          <h3 className="font-bold text-xl mb-4 border-b border-slate-700 pb-2">Changes & AI Summary ({changes.length})</h3>
          
          {changes.length === 0 ? (
            <p className="text-slate-400 text-sm">No changes found for this release.</p>
          ) : (
            <div className="space-y-4">
              {changes.map((change) => (
                <div key={change.id} className="bg-slate-700/30 p-4 rounded-lg border border-slate-600">
                  <div className="flex justify-between items-start mb-2">
                    <div className="flex items-center gap-3">
                      <span className={`px-2 py-1 rounded text-xs font-bold uppercase ${
                        change.category === 'breaking' ? 'bg-red-500/20 text-red-400' :
                        change.category === 'feature' ? 'bg-green-500/20 text-green-400' :
                        change.category === 'bug fix' ? 'bg-blue-500/20 text-blue-400' :
                        'bg-slate-600 text-slate-300'
                      }`}>
                        {change.category}
                      </span>
                      {change.is_breaking && (
                        <span className="bg-red-500/20 text-red-400 px-2 py-1 rounded text-xs font-bold">
                          ⚠️ BREAKING
                        </span>
                      )}
                    </div>
                    <a href={change.url} target="_blank" rel="noreferrer" className="text-sm text-blue-400 hover:underline">
                      {change.sha.substring(0, 7)}
                    </a>
                  </div>
                  
                  <h4 className="font-semibold text-white mb-1">{change.title}</h4>
                  <p className="text-slate-400 text-sm mb-2">{change.summary}</p>
                  
                  {change.breaking_reason && (
                    <p className="text-red-400 text-sm bg-red-500/10 p-2 rounded mt-2">
                      <strong>Breaking Reason:</strong> {change.breaking_reason}
                    </p>
                  )}
                  
                  <div className="text-xs text-slate-500 mt-2">
                    Author: {change.author} | Files: {change.files?.join(', ')}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}