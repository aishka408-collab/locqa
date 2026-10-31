import React, { useState, useEffect } from 'react'

export default function App() {
  const [activeTab, setActiveTab] = useState<'analysis' | 'glossary' | 'export'>('analysis')
  const [stats, setStats] = useState({ total_keys: 0, error_count: 0, accepted_fixes: 0, rejected_fixes: 0 })
  const [fileIds, setFileIds] = useState<string[]>([])
  const [sourceFid, setSourceFid] = useState('')
  const [targetFid, setTargetFid] = useState('')
  const [analysisId, setAnalysisId] = useState<string | null>(null)
  const [errors, setErrors] = useState<any[]>([])
  const [catFilter, setCatFilter] = useState('')
  const [confFilter, setConfFilter] = useState('')
  const [searchTerm, setSearchTerm] = useState('')
  const [glossary, setGlossary] = useState<Record<string, any>>({})
  const [newRu, setNewRu] = useState('')
  const [newKk, setNewKk] = useState('')
  const [newVars, setNewVars] = useState('')

  useEffect(() => {
    loadGlossary()
  }, [])

  const loadGlossary = async () => {
    try {
      const res = await fetch('/glossary')
      if (res.ok) setGlossary(await res.json())
    } catch (e) {
      console.error(e)
    }
  }

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files) return
    const fd = new FormData()
    for (let i = 0; i < e.target.files.length; i++) {
      fd.append('files', e.target.files[i])
    }
    const res = await fetch('/upload', { method: 'POST', body: fd })
    if (res.ok) {
      const data = await res.json()
      setFileIds(data.file_ids)
      const ru = data.file_ids.find((f: string) => f.includes('ru')) || data.file_ids[0] || ''
      const kk = data.file_ids.find((f: string) => f.includes('kk')) || data.file_ids[1] || ''
      setSourceFid(ru)
      setTargetFid(kk)
    }
  }

  const runAnalysis = async () => {
    const res = await fetch('/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids: fileIds, source_file_id: sourceFid, target_file_id: targetFid })
    })
    if (res.ok) {
      const data = await res.json()
      setAnalysisId(data.analysis_id)
      setErrors(data.errors || [])
      fetchStats(data.analysis_id)
    }
  }

  const fetchStats = async (id: string) => {
    const res = await fetch(`/analysis/${id}/stats`)
    if (res.ok) setStats(await res.json())
  }

  const decideFix = async (key: string, action: string, newVal: string | null) => {
    if (!analysisId) return
    await fetch(`/analysis/${analysisId}/fixes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify([{ key, action, new_value: newVal }])
    })
    fetchStats(analysisId)
  }

  const applyFixes = async () => {
    if (!analysisId) return
    await fetch(`/analysis/${analysisId}/apply`, { method: 'POST' })
    alert('Исправления применены!')
    fetchStats(analysisId)
  }

  const undoLast = async () => {
    if (!analysisId) return
    await fetch(`/analysis/${analysisId}/undo`, { method: 'POST' })
    alert('Откат выполнен!')
    fetchStats(analysisId)
  }

  const addTerm = async () => {
    if (!newRu || !newKk) return
    await fetch('/glossary', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ru_term: newRu, kk: newKk, variants: newVars.split(',').map(s => s.trim()).filter(Boolean) })
    })
    setNewRu('')
    setNewKk('')
    setNewVars('')
    loadGlossary()
  }

  const deleteTerm = async (ru: string) => {
    await fetch(`/glossary/${encodeURIComponent(ru)}`, { method: 'DELETE' })
    loadGlossary()
  }

  const filteredErrors = errors.filter(e => {
    if (catFilter && e.category !== catFilter) return false
    if (confFilter && e.confidence !== confFilter) return false
    if (searchTerm) {
      const match = e.key.toLowerCase().includes(searchTerm.toLowerCase()) ||
                    (e.source || e.original || '').toLowerCase().includes(searchTerm.toLowerCase())
      if (!match) return false
    }
    return true
  })

  return (
    <div style={{ padding: 24, fontFamily: 'sans-serif', maxWidth: 1200, margin: '0 auto' }}>
      <h1>🇰🇿 Qazaq Loc QA — Панель управления</h1>
      <div style={{ display: 'flex', gap: 16, margin: '16px 0' }}>
        <button onClick={() => setActiveTab('analysis')}>Анализ</button>
        <button onClick={() => setActiveTab('glossary')}>Глоссарий</button>
        <button onClick={() => setActiveTab('export')}>Экспорт</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
        <div style={{ padding: 12, border: '1px solid #ccc', borderRadius: 8 }}>Всего ключей: <strong>{stats.total_keys}</strong></div>
        <div style={{ padding: 12, border: '1px solid #ccc', borderRadius: 8 }}>Ошибок: <strong>{stats.error_count}</strong></div>
        <div style={{ padding: 12, border: '1px solid #ccc', borderRadius: 8 }}>Принято: <strong>{stats.accepted_fixes}</strong></div>
        <div style={{ padding: 12, border: '1px solid #ccc', borderRadius: 8 }}>Отклонено: <strong>{stats.rejected_fixes}</strong></div>
      </div>

      {activeTab === 'analysis' && (
        <div>
          <div style={{ marginBottom: 16, padding: 16, border: '1px dashed #999', borderRadius: 8 }}>
            <input type="file" multiple onChange={handleUpload} />
            {fileIds.length > 0 && (
              <div style={{ marginTop: 12 }}>
                <label>Source: <select value={sourceFid} onChange={e => setSourceFid(e.target.value)}>{fileIds.map(f => <option key={f} value={f}>{f}</option>)}</select></label>
                <label style={{ marginLeft: 12 }}>Target: <select value={targetFid} onChange={e => setTargetFid(e.target.value)}>{fileIds.map(f => <option key={f} value={f}>{f}</option>)}</select></label>
                <button style={{ marginLeft: 16 }} onClick={runAnalysis}>Запустить анализ (11 категорий)</button>
              </div>
            )}
          </div>

          {analysisId && (
            <div>
              <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
                <button onClick={applyFixes}>Применить исправления</button>
                <button onClick={undoLast}>Undo</button>
                <a href={`/analysis/${analysisId}/download/${targetFid}`} target="_blank" rel="noreferrer"><button>Скачать ARB</button></a>
                <select value={catFilter} onChange={e => setCatFilter(e.target.value)}>
                  <option value="">Все категории</option>
                  <option value="untranslated">untranslated</option>
                  <option value="same_as_source">same_as_source</option>
                  <option value="placeholder_mismatch">placeholder_mismatch</option>
                  <option value="plural_mismatch">plural_mismatch</option>
                  <option value="glossary_violation">glossary_violation</option>
                  <option value="length_overflow">length_overflow</option>
                  <option value="machine_translation_suspect">machine_translation_suspect</option>
                  <option value="inconsistent_term">inconsistent_term</option>
                  <option value="suffix_harmony">suffix_harmony</option>
                  <option value="orthography">orthography</option>
                  <option value="wrong_language">wrong_language</option>
                </select>
                <input placeholder="Поиск..." value={searchTerm} onChange={e => setSearchTerm(e.target.value)} />
              </div>

              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ background: '#eee' }}>
                    <th style={{ padding: 8, border: '1px solid #ddd' }}>Key</th>
                    <th style={{ padding: 8, border: '1px solid #ddd' }}>Категория</th>
                    <th style={{ padding: 8, border: '1px solid #ddd' }}>Исходник</th>
                    <th style={{ padding: 8, border: '1px solid #ddd' }}>Перевод</th>
                    <th style={{ padding: 8, border: '1px solid #ddd' }}>Предложение</th>
                    <th style={{ padding: 8, border: '1px solid #ddd' }}>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredErrors.map((err, idx) => (
                    <tr key={idx}>
                      <td style={{ padding: 8, border: '1px solid #ddd' }}>{err.key}</td>
                      <td style={{ padding: 8, border: '1px solid #ddd' }}>{err.category}</td>
                      <td style={{ padding: 8, border: '1px solid #ddd' }}>{err.source || err.original}</td>
                      <td style={{ padding: 8, border: '1px solid #ddd' }}>{err.current}</td>
                      <td style={{ padding: 8, border: '1px solid #ddd' }}>{err.suggestion || '-'}</td>
                      <td style={{ padding: 8, border: '1px solid #ddd' }}>
                        <button onClick={() => decideFix(err.key, 'accept', err.suggestion || err.current)}>✓</button>
                        <button onClick={() => decideFix(err.key, 'reject', null)}>✕</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {activeTab === 'glossary' && (
        <div>
          <h3>Глоссарий</h3>
          <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
            <input placeholder="Русский термин" value={newRu} onChange={e => setNewRu(e.target.value)} />
            <input placeholder="Казахский перевод" value={newKk} onChange={e => setNewKk(e.target.value)} />
            <input placeholder="Варианты через запятую" value={newVars} onChange={e => setNewVars(e.target.value)} />
            <button onClick={addTerm}>Добавить</button>
          </div>
          <ul>
            {Object.entries(glossary).map(([ru, item]: any) => (
              <li key={ru}>
                <strong>{ru}</strong> → {item.kk} (варианты: {(item.variants || []).join(', ')})
                <button style={{ marginLeft: 8 }} onClick={() => deleteTerm(ru)}>Удалить</button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {activeTab === 'export' && (
        <div>
          <h3>Экспорт отчётов</h3>
          {analysisId ? (
            <div style={{ display: 'flex', gap: 12 }}>
              <a href={`/analysis/${analysisId}/export/markdown`} target="_blank" rel="noreferrer"><button>Скачать Markdown (.md)</button></a>
              <a href={`/analysis/${analysisId}/export/csv`} target="_blank" rel="noreferrer"><button>Скачать CSV (.csv)</button></a>
              <a href={`/analysis/${analysisId}/report`} target="_blank" rel="noreferrer"><button>Скачать JSON</button></a>
            </div>
          ) : (
            <p>Сначала запустите анализ.</p>
          )}
        </div>
      )}
    </div>
  )
}
