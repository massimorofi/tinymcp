import { useState, useEffect, useRef } from 'react'
import './App.css'

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

function App() {
  const [servers, setServers] = useState([])
  const [session, setSession] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [expandedServers, setExpandedServers] = useState({})
  const [selectedTool, setSelectedTool] = useState(null)
  const [tools, setTools] = useState({})
  const [executing, setExecuting] = useState(false)
  const [paramValues, setParamValues] = useState({})
  const [results, setResults] = useState({})
  const resultsEndRef = useRef(null)
  const [activeTab, setActiveTab] = useState('servers')
  const [registryServers, setRegistryServers] = useState([])
  const [activeRegistryServers, setActiveRegistryServers] = useState([])
  const [selectedRegistry, setSelectedRegistry] = useState({})
  const [registryLoading, setRegistryLoading] = useState(false)
  const [registryError, setRegistryError] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedCard, setSelectedCard] = useState(null)
  const [latestOnly, setLatestOnly] = useState(false)
  const [showSelectedOnly, setShowSelectedOnly] = useState(false)
  const [nextCursor, setNextCursor] = useState(null)

  const fetchServers = async () => {
    try {
      const res = await fetch(`${API_BASE}/registry/servers`)
      if (!res.ok) throw new Error('Failed to fetch servers')
      const data = await res.json()
      setServers(data)
    } catch (err) {
      setError(err.message)
    }
  }

  const fetchRegistryServers = async (append = false, cursor = null) => {
    setRegistryLoading(true)
    setRegistryError(null)
    try {
      let url = `${API_BASE}/registry/external?limit=100`
      if (latestOnly) url += `&latest_only=true`
      if (searchQuery) url += `&search=${encodeURIComponent(searchQuery)}`
      if (cursor) url += `&cursor=${encodeURIComponent(cursor)}`
      
      console.log('Fetching registry servers:', url)
      const res = await fetch(url)
      if (!res.ok) throw new Error(`Failed to fetch: ${res.status}`)
      const data = await res.json()
      console.log('Got servers:', data.servers?.length || 0, 'nextCursor:', data.nextCursor)
      
      if (append && cursor) {
        setRegistryServers(prev => [...prev, ...(data.servers || [])])
      } else {
        setRegistryServers(data.servers || [])
      }
      setNextCursor(data.nextCursor || data.metadata?.nextCursor)
    } catch (err) {
      console.error('Error fetching registry servers:', err)
      setRegistryError(err.message)
    } finally {
      setRegistryLoading(false)
    }
  }

  const loadMoreServers = async () => {
    if (nextCursor && !registryLoading) {
      await fetchRegistryServers(true, nextCursor)
    }
  }

  const fetchActiveRegistryServers = async () => {
    try {
      const res = await fetch(`${API_BASE}/registry/external/active`)
      if (!res.ok) throw new Error('Failed to fetch active servers')
      const data = await res.json()
      setActiveRegistryServers(data)
      const activeMap = {}
      data.forEach(s => { activeMap[s.name] = true })
      setSelectedRegistry(activeMap)
    } catch (err) {
      console.error('Error fetching active servers:', err)
    }
  }

  const initSession = async () => {
    try {
      const res = await fetch(`${API_BASE}/sessions`, { method: 'POST' })
      if (!res.ok) throw new Error('Failed to create session')
      const data = await res.json()
      setSession(data)
    } catch (err) {
      setError(err.message)
    }
  }

  useEffect(() => {
    const init = async () => {
      setLoading(true)
      await fetchServers()
      await initSession()
      setLoading(false)
    }
    init()
  }, [])

  useEffect(() => {
    if (activeTab === 'registry' && registryServers.length === 0) {
      fetchRegistryServers()
      fetchActiveRegistryServers()
    } else if (activeTab === 'registry') {
      fetchActiveRegistryServers()
    }
  }, [activeTab])

  useEffect(() => {
    if (activeTab === 'registry' && latestOnly !== undefined) {
      fetchRegistryServers()
    }
  }, [latestOnly])

  useEffect(() => {
    if (activeTab === 'registry' && searchQuery) {
      const timer = setTimeout(() => {
        fetchRegistryServers()
      }, 300)
      return () => clearTimeout(timer)
    }
  }, [searchQuery])

  useEffect(() => {
    const interval = setInterval(() => {
      fetchServers()
    }, 5000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    if (resultsEndRef.current) {
      resultsEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [results])

  const toggleServer = async (serverId) => {
    const newExpanded = { ...expandedServers }
    newExpanded[serverId] = !newExpanded[serverId]
    setExpandedServers(newExpanded)
    
    if (!tools[serverId]) {
      await fetchTools(serverId)
    }
  }

  const fetchTools = async (serverId) => {
    if (!session) return
    
    try {
      const res = await fetch(`${API_BASE}/execute?server=${encodeURIComponent(serverId)}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Session-ID': session.sessionId
        },
        body: JSON.stringify({
          jsonrpc: '2.0',
          method: 'tools/list',
          params: {},
          id: 1
        })
      })
      
      if (res.ok) {
        const data = await res.json()
        const toolList = data.result?.tools || data.result?.result?.tools || []
        setTools(prev => ({ ...prev, [serverId]: toolList }))
      }
    } catch (err) {
      console.error('Failed to fetch tools:', err)
    }
  }

  const selectTool = (serverId, tool) => {
    setSelectedTool({ server: serverId, tool: tool.name, description: tool.description, inputSchema: tool.inputSchema })
    setParamValues({})
  }

  const handleParamChange = (paramName, value) => {
    setParamValues(prev => ({ ...prev, [paramName]: value }))
  }

  const executeTool = async () => {
    if (!session || !selectedTool) return
    setExecuting(true)
    
    const args = {}
    if (selectedTool.inputSchema?.properties) {
      Object.keys(selectedTool.inputSchema.properties).forEach(param => {
        const paramConfig = selectedTool.inputSchema.properties[param]
        let value = paramValues[param]
        if (value !== undefined && value !== '') {
          if (paramConfig.type === 'integer' || paramConfig.type === 'number') {
            args[param] = Number(value)
          } else {
            args[param] = value
          }
        } else if (paramConfig.default !== undefined) {
          args[param] = paramConfig.default
        }
      })
    }
    
    try {
      const res = await fetch(`${API_BASE}/execute?server=${encodeURIComponent(selectedTool.server)}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Session-ID': session.sessionId
        },
        body: JSON.stringify({
          jsonrpc: '2.0',
          method: 'tools/call',
          params: { name: selectedTool.tool, arguments: args },
          id: Date.now()
        })
      })
      
      const data = await res.json()
      const resultKey = `${selectedTool.server}:${selectedTool.tool}`
      setResults(prev => {
        const newResults = { ...prev }
        if (!newResults[resultKey]) {
          newResults[resultKey] = []
        }
        newResults[resultKey] = [...newResults[resultKey], {
          timestamp: new Date().toISOString(),
          request: args,
          response: data.result || data.error || data
        }]
        return newResults
      })
    } catch (err) {
      setResults(prev => ({
        ...prev,
        [`${selectedTool.server}:${selectedTool.tool}`]: [{ error: err.message }]
      }))
    }
    
    setExecuting(false)
  }

  const handleRegistryCheckbox = (serverName) => {
    setSelectedRegistry(prev => ({
      ...prev,
      [serverName]: !prev[serverName]
    }))
  }

  const handleCardClick = (serverName) => {
    setSelectedCard(selectedCard === serverName ? null : serverName)
  }

  const selectAllRegistry = () => {
    const allSelected = {}
    filteredRegistryServers.forEach(s => { allSelected[s.name] = true })
    setSelectedRegistry(allSelected)
  }

  const clearAllRegistry = () => {
    setSelectedRegistry({})
  }

  const filteredRegistryServers = registryServers.filter(server => {
    if (showSelectedOnly) {
      const isCheckedInUI = selectedRegistry[server.name]
      const isSavedActive = activeRegistryServers.some(s => s.name === server.name)
      if (!isCheckedInUI && !isSavedActive) {
        return false
      }
    }
    const query = searchQuery.toLowerCase()
    const nameMatch = (server.title || server.name || '').toLowerCase().includes(query)
    const descMatch = (server.description || '').toLowerCase().includes(query)
    return nameMatch || descMatch
  })

  const saveSelectedServers = async () => {
    const toActivate = Object.keys(selectedRegistry).filter(name => selectedRegistry[name])
    const toDeactivate = activeRegistryServers
      .map(s => s.name)
      .filter(name => !selectedRegistry[name])
    
    console.log('Saving servers - toActivate:', toActivate, 'toDeactivate:', toDeactivate)
    
    if (toActivate.length === 0 && toDeactivate.length === 0) {
      alert('No changes to save')
      return
    }
    
    try {
      if (toActivate.length > 0) {
        console.log('Sending activate request with:', JSON.stringify({ servers: toActivate }))
        const res = await fetch(`${API_BASE}/registry/external/activate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ servers: toActivate })
        })
        const data = await res.json()
        console.log('Activate response:', data)
        alert(`Activated: ${data.activated?.join(', ') || 'none'}`)
      }
      if (toDeactivate.length > 0) {
        const res = await fetch(`${API_BASE}/registry/external/deactivate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ servers: toDeactivate })
        })
        const data = await res.json()
        console.log('Deactivate response:', data)
        alert(`Deactivated: ${data.deactivated?.join(', ') || 'none'}`)
      }
      await fetchActiveRegistryServers()
      await fetchServers()
    } catch (err) {
      console.error('Error saving servers:', err)
      alert('Error saving servers: ' + err.message)
    }
  }

  const getStatusColor = (status) => {
    return status === 'connected' ? '#4ade80' : '#ef4444'
  }

  const unregisterServer = async (serverId) => {
    if (!confirm(`Unregister server "${serverId}"?`)) return
    try {
      const res = await fetch(`${API_BASE}/registry/servers/${encodeURIComponent(serverId)}`, {
        method: 'DELETE'
      })
      if (res.ok) {
        await fetchServers()
        await fetchActiveRegistryServers()
      }
    } catch (err) {
      console.error('Error unregistering server:', err)
    }
  }

  const unregisterAllServers = async () => {
    const registryServers = servers.filter(s => s.command || s.url || s.identifier)
    if (registryServers.length === 0) {
      alert('No servers to unregister')
      return
    }
    if (!confirm(`Unregister all ${registryServers.length} servers?`)) return
    try {
      const res = await fetch(`${API_BASE}/registry/servers`, {
        method: 'DELETE'
      })
      if (res.ok) {
        await fetchServers()
        await fetchActiveRegistryServers()
      }
    } catch (err) {
      console.error('Error unregistering all servers:', err)
    }
  }

  const handleServerContextMenu = (e, serverId) => {
    e.preventDefault()
    const registryServers = servers.filter(s => s.command || s.url || s.identifier)
    const isRegistry = registryServers.some(s => s.id === serverId)
    if (isRegistry) {
      unregisterServer(serverId)
    }
  }

  if (loading) return <div className="loading">Loading MCP Gateway...</div>
  if (error) return <div className="error">Error: {error}</div>

  const currentResults = selectedTool ? results[`${selectedTool.server}:${selectedTool.tool}`] || [] : []
  const hasParams = selectedTool?.inputSchema?.properties && Object.keys(selectedTool.inputSchema.properties).length > 0

  return (
    <div className="app">
      <header className="header">
        <h1>MCP Gateway</h1>
        <div className="header-info">
          <span>Session: {session?.sessionId?.slice(0, 8)}...</span>
          <button onClick={() => window.location.reload()}>Refresh</button>
        </div>
      </header>

      <nav className="tabs">
        <button 
          className={`tab ${activeTab === 'servers' ? 'active' : ''}`}
          onClick={() => setActiveTab('servers')}
        >
          Servers
        </button>
        <button 
          className={`tab ${activeTab === 'registry' ? 'active' : ''}`}
          onClick={() => setActiveTab('registry')}
        >
          Registry Admin
        </button>
      </nav>

      {activeTab === 'servers' && (
        <main className="main">
          <aside className="sidebar">
            <div className="sidebar-header">
              <h2>MCP Servers</h2>
              <button className="unregister-all-btn" onClick={unregisterAllServers} title="Unregister all registered servers">
                ×
              </button>
            </div>
            <div className="server-tree">
              {servers.length === 0 ? (
                <p className="no-data">No servers configured</p>
              ) : (
                servers.map(server => (
                  <div key={server.id} className="server-item">
                    <div 
                      className={`server-row ${selectedTool?.server === server.id ? 'selected' : ''}`}
                      onClick={() => toggleServer(server.id)}
                      onContextMenu={(e) => handleServerContextMenu(e, server.id)}
                    >
                      <span className="status-icon" style={{ color: getStatusColor(server.status) }}>
                        {server.status === 'connected' ? '●' : '○'}
                      </span>
                      <span className="expand-icon">
                        {expandedServers[server.id] ? '▼' : '▶'}
                      </span>
                      <span className="server-name">{server.id}</span>
                    </div>
                    {expandedServers[server.id] && (
                      <div className="tools-list">
                        {(tools[server.id] || []).map(tool => (
                          <div 
                            key={tool.name}
                            className={`tool-row ${selectedTool?.server === server.id && selectedTool?.tool === tool.name ? 'selected' : ''}`}
                            onClick={() => selectTool(server.id, tool)}
                          >
                            <span className="tool-name">⚡ {tool.name}</span>
                          </div>
                        ))}
                        {(!tools[server.id] || tools[server.id].length === 0) && (
                          <div className="no-tools">Loading tools...</div>
                        )}
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
          </aside>

          <section className="results-panel">
            <div className="panel-header">
              <h2>{selectedTool ? `${selectedTool.server} / ${selectedTool.tool}` : 'Results'}</h2>
            </div>
            
            {selectedTool && (
              <div className="params-form">
                <div className="params-header">
                  <span>Parameters</span>
                </div>
                {hasParams ? (
                  <div className="params-fields">
                    {Object.entries(selectedTool.inputSchema.properties).map(([paramName, paramConfig]) => (
                      <div key={paramName} className="param-field">
                        <label>
                          {paramName}
                          {paramConfig.description && <span className="param-desc"> - {paramConfig.description}</span>}
                          {paramConfig.default !== undefined && <span className="param-default"> (default: {paramConfig.default})</span>}
                        </label>
                        <input
                          type={paramConfig.type === 'integer' || paramConfig.type === 'number' ? 'number' : 'text'}
                          value={paramValues[paramName] || ''}
                          onChange={(e) => handleParamChange(paramName, e.target.value)}
                          placeholder={paramConfig.default?.toString() || ''}
                        />
                      </div>
                    ))}
                    <button 
                      className="execute-btn" 
                      onClick={executeTool}
                      disabled={executing}
                    >
                      {executing ? 'Executing...' : 'Execute'}
                    </button>
                  </div>
                ) : (
                  <div className="no-params">
                    <p>No parameters required</p>
                    <button 
                      className="execute-btn" 
                      onClick={executeTool}
                      disabled={executing}
                    >
                      {executing ? 'Executing...' : 'Execute'}
                    </button>
                  </div>
                )}
              </div>
            )}
            
            <div className="results-content">
              {currentResults.length > 0 ? (
                currentResults.map((result, idx) => (
                  <div key={idx} className="result-block">
                    <div className="result-meta">
                      Request #{idx + 1} • {new Date(result.timestamp || Date.now()).toLocaleTimeString()}
                    </div>
                    <div className="result-section">
                      <div className="result-label">request</div>
                      <pre className="result-json">{JSON.stringify(result.request, null, 2)}</pre>
                    </div>
                    <div className="result-section">
                      <div className="result-label">response</div>
                      <pre className="result-json">{JSON.stringify(result.response, null, 2)}</pre>
                    </div>
                  </div>
                ))
              ) : (
                <p className="no-data">
                  {selectedTool 
                    ? 'Click Execute to run the tool' 
                    : 'Select a tool from the left menu to execute'}
                </p>
              )}
              <div ref={resultsEndRef} />
            </div>
          </section>
        </main>
      )}

      {activeTab === 'registry' && (
        <main className="main registry-main">
          <div className="registry-header">
            <div className="registry-info">
              <span className="registry-source">Source: MCP Registry (registry.modelcontextprotocol.io)</span>
              <label className="latest-toggle">
                <input
                  type="checkbox"
                  checked={latestOnly}
                  onChange={(e) => setLatestOnly(e.target.checked)}
                />
                Latest versions
              </label>
              <label className="latest-toggle">
                <input
                  type="checkbox"
                  checked={showSelectedOnly}
                  onChange={(e) => setShowSelectedOnly(e.target.checked)}
                />
                Selected only
              </label>
              <span className="registry-count">Total: {filteredRegistryServers.length} servers</span>
            </div>
            <div className="registry-search">
              <input
                type="text"
                placeholder="Search by name or description..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="search-input"
              />
            </div>
          </div>
          <div className="registry-header-actions">
            <button className="action-btn" onClick={selectAllRegistry}>Select All</button>
            <button className="action-btn" onClick={clearAllRegistry}>Clear All</button>
            <button className="save-btn" onClick={saveSelectedServers}>Save Selection</button>
            {nextCursor && (
              <button className="action-btn" onClick={loadMoreServers} disabled={registryLoading}>
                Load More
              </button>
            )}
          </div>
          
          {registryLoading ? (
            <div className="loading">Loading registry servers...</div>
          ) : registryError ? (
            <div className="error">Error: {registryError}</div>
          ) : (
            <div className="registry-grid">
              {filteredRegistryServers.map(server => (
                <div 
                  key={server.name} 
                  className={`registry-card ${selectedRegistry[server.name] ? 'selected' : ''} ${selectedCard === server.name ? 'expanded' : ''}`}
                  onClick={() => handleCardClick(server.name)}
                >
                  <div className="registry-card-header">
                    <input
                      type="checkbox"
                      checked={selectedRegistry[server.name] || false}
                      onChange={(e) => {
                        e.stopPropagation()
                        handleRegistryCheckbox(server.name)
                      }}
                      onClick={(e) => e.stopPropagation()}
                    />
                    <span className="registry-name">{server.title || server.name}</span>
                  </div>
                  <div className="registry-description">{server.description}</div>
                  <div className="registry-meta">
                    <span className="registry-version">v{server.version}</span>
                    {server.registryType && <span className="registry-type">{server.registryType}</span>}
                  </div>
                  {selectedCard === server.name && (
                    <div className="registry-tooltip">
                      <strong>Full Description:</strong>
                      <p>{server.description}</p>
                      {server.identifier && <p><strong>Package:</strong> {server.identifier}</p>}
                      {server.transport?.url && <p><strong>URL:</strong> {server.transport.url}</p>}
                      {server.websiteUrl && <p><strong>Website:</strong> <a href={server.websiteUrl} target="_blank" rel="noopener noreferrer">{server.websiteUrl}</a></p>}
                      {server.repository && <p><strong>Repository:</strong> <a href={server.repository} target="_blank" rel="noopener noreferrer">{server.repository}</a></p>}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </main>
      )}
    </div>
  )
}

export default App
