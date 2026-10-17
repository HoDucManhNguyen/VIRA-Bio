'use strict';

const $ = id => document.getElementById(id);
const state = { indexing: false, answering: false, searching: false, libraryLoaded: false,
  noteId: null, view: 'instructions', exploreMode: 'answer' };

function toast(message) {
  const box = $('toast');
  box.textContent = message;
  box.style.display = 'block';
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => box.style.display = 'none', 4200);
}

async function api(path, options = {}) {
  const response = await fetch('/api' + path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
  });
  let data;
  try { data = await response.json(); }
  catch { throw new Error('Unexpected server response'); }
  if (!response.ok) throw new Error(data.detail || 'Request failed');
  return data;
}

function wrap(fn) {
  return async (...args) => {
    try { await fn(...args); }
    catch (error) { toast(error.message || String(error)); }
  };
}

function make(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function closeMenu() {
  $('sidebar').classList.remove('open');
  $('mobileMenu').setAttribute('aria-expanded', 'false');
}

function setView(view) {
  state.view = view;
  for (const name of ['instructions', 'library', 'notes', 'tasks']) {
    $(name + 'View').classList.toggle('hidden', name !== view);
    document.querySelector(`[data-view="${name}"]`).classList.toggle('active', name === view);
  }
  const labels = {
    instructions: ['GET STARTED', 'Start with your PDF library.'],
    library: ['RESEARCH WORKSPACE', 'PDF Library'],
    notes: ['KNOWLEDGE', 'Your notes.'],
    tasks: ['PRODUCTIVITY', 'Stay on track.']
  };
  $('sectionLabel').textContent = labels[view][0];
  $('sectionTitle').textContent = labels[view][1];
  closeMenu();
  if (view === 'notes') wrap(loadNotes)();
  if (view === 'tasks') wrap(loadTasks)();
  if (view === 'library') wrap(loadLibrary)();
}

function sourceCard(source, index) {
  const card = make('div', undefined, 'source-card');
  const link = make('a', `[${index}] ${source.title} · page ${source.page}`);
  link.href = `/api/library/${encodeURIComponent(source.paper_id)}/pdf#page=${source.page}`;
  link.target = '_blank';
  link.rel = 'noopener';
  const snippet = source.snippet || '';
  const wordEnd = snippet.lastIndexOf(' ', 280);
  const previewText = snippet.length > 280 ?
    snippet.slice(0, wordEnd > 180 ? wordEnd : 280).trimEnd() + '…' : snippet;
  const preview = make('p', previewText);
  card.append(link, preview);
  if (snippet.length > 280) {
    const details = make('details', undefined, 'source-more');
    details.append(make('summary', 'Read full excerpt'), make('p', snippet));
    details.ontoggle = () => { preview.hidden = details.open; };
    card.append(details);
  }
  return card;
}

function setExploreMode(mode) {
  state.exploreMode = mode;
  const answer = mode === 'answer';
  $('exploreAnswer').setAttribute('aria-selected', String(answer));
  $('exploreSearch').setAttribute('aria-selected', String(!answer));
  $('answerPanel').hidden = !answer;
  $('searchPanel').hidden = answer;
  $(answer ? 'answerQuestion' : 'libraryQuery').focus();
}

async function loadLibrary() {
  const papers = await api('/library');
  const list = $('libraryList');
  list.replaceChildren();
  $('documentCount').textContent = `${papers.length} ${papers.length === 1 ? 'PDF' : 'PDFs'}`;
  $('answerButton').disabled = !papers.length || state.answering;
  $('searchButton').disabled = !papers.length || state.searching;
  if (!state.libraryLoaded) {
    $('documentsDetails').open = !papers.length;
    $('importDetails').open = !papers.length;
    state.libraryLoaded = true;
  }
  if (!papers.length) list.append(make('p', 'No PDFs yet. Add one below to start finding evidence.', 'empty-library'));
  for (const paper of papers) {
    const row = make('div', undefined, 'paper-row');
    const link = make('a', paper.title);
    link.href = `/api/library/${encodeURIComponent(paper.id)}/pdf`;
    link.target = '_blank';
    link.rel = 'noopener';
    const details = make('small', `${paper.page_count} pages · ${paper.filename}`);
    const remove = make('button', 'Delete');
    remove.type = 'button';
    remove.onclick = wrap(async () => {
      if (!confirm(`Delete “${paper.title}” from your library?`)) return;
      await api('/library/' + encodeURIComponent(paper.id), { method: 'DELETE' });
      await loadLibrary();
    });
    row.append(link, details, remove);
    list.append(row);
  }
  await refreshSemanticStatus();
}

async function refreshSemanticStatus() {
  const status = await api('/retrieval/status');
  const ready = status.ready;
  for (const id of ['answerSearchMode', 'librarySearchMode']) {
    const select = $(id);
    select.querySelector('option[value="hybrid"]').disabled = !ready;
    if (!ready && select.value === 'hybrid') select.value = 'keyword';
  }
  $('semanticStatus').textContent = !status.total ? 'Import a PDF before building the index.' :
    ready ? `Hybrid search is ready (${status.indexed}/${status.total} passages; ${status.model}).` :
      `${status.indexed}/${status.total} passages indexed with ${status.model}. Install this embedding model in Ollama, then build the index.`;
  $('semanticSummary').textContent = ready ? 'Hybrid ready' : 'Keyword available';
  $('retrievalBadge').textContent = ready ? 'Hybrid available' : 'Keyword search';
  $('buildSemanticIndex').disabled = state.indexing || !status.total;
  $('buildSemanticIndex').textContent = ready ? 'Rebuild semantic index' : 'Build semantic index';
  return status;
}

async function buildSemanticIndex() {
  state.indexing = true;
  $('buildSemanticIndex').disabled = true;
  try {
    let status = await api('/retrieval/status');
    if (status.ready) status = await api('/retrieval/reset', { method: 'POST' });
    while (status.total && !status.ready) {
      const previous = status.indexed;
      status = await api('/retrieval/index', { method: 'POST' });
      $('semanticStatus').textContent = `Building local index: ${status.indexed}/${status.total} passages…`;
      if (status.indexed <= previous) throw new Error('Indexing made no progress. Check the local embedding model.');
    }
    toast('Semantic index is ready. Hybrid search is available.');
  } finally {
    state.indexing = false;
    await refreshSemanticStatus();
  }
}

async function importPaper() {
  const files = [...$('pdfFile').files];
  if (!files.length || !$('rightsConfirmed').checked) return;
  $('importButton').disabled = true;
  try {
    let added = 0;
    const errors = [];
    for (const [index, file] of files.entries()) {
      $('importButton').textContent = `Importing ${index + 1}/${files.length}`;
      try {
        if (file.size > 25 * 1024 * 1024) throw new Error('exceeds the 25 MB limit');
        const response = await fetch('/api/library/import', {
          method: 'POST',
          headers: { 'Content-Type': 'application/pdf', 'X-Filename': encodeURIComponent(file.name),
            'X-Rights-Confirmed': 'true' },
          body: file
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || 'could not import this PDF');
        if (result.created) added++;
      } catch (error) { errors.push(`${file.name}: ${error.message}`); }
    }
    toast(`Imported ${added}/${files.length} PDFs.${errors.length ? ' Errors: ' + errors.slice(0, 2).join('; ') : ''}`);
    $('importForm').reset();
    if (added) {
      $('importDetails').open = false;
      $('documentsDetails').open = false;
    }
    await loadLibrary();
  } finally { $('importButton').disabled = false; $('importButton').textContent = 'Import PDFs'; }
}

async function searchLibrary() {
  const q = $('libraryQuery').value.trim();
  const results = $('searchResults');
  if (!q || state.searching) return;
  state.searching = true;
  $('searchButton').disabled = true;
  results.replaceChildren(make('p', 'Searching PDF passages…'));
  try {
    const matches = await api('/library/search?q=' + encodeURIComponent(q) +
      '&mode=' + encodeURIComponent($('librarySearchMode').value));
    results.replaceChildren();
    if (!matches.length) results.append(make('p', 'No matching passages found. Try another term.'));
    else {
      results.append(make('h4', `${matches.length} matching ${matches.length === 1 ? 'passage' : 'passages'}`));
      matches.forEach((source, index) => results.append(sourceCard(source, index + 1)));
    }
  } catch (error) {
    results.replaceChildren(make('p', error.message || String(error), 'answer-error'));
  } finally {
    state.searching = false;
    $('searchButton').disabled = false;
  }
}

async function answerFromLibrary() {
  const question = $('answerQuestion').value.trim();
  if (!question || state.answering) return;
  const results = $('answerResults');
  state.answering = true;
  $('answerButton').disabled = true;
  results.replaceChildren(make('p', 'Finding PDF evidence…'));
  try {
    const answer = await api('/library/answer', {
      method: 'POST',
      body: JSON.stringify({ question, mode: $('answerSearchMode').value })
    });
    results.replaceChildren(make('p', answer.answer, 'answer-text'));
    if (answer.sources.length) {
      results.append(make('h4', 'PDF evidence'));
      answer.sources.forEach((source, index) => results.append(sourceCard(source, index + 1)));
    }
  } catch (error) {
    results.replaceChildren(make('p', error.message || String(error), 'answer-error'));
  } finally {
    state.answering = false;
    $('answerButton').disabled = false;
  }
}

async function searchPubMed() {
  const q = $('pubmedQuery').value.trim();
  const results = $('pubmedResults');
  results.replaceChildren();
  if (!q) return;
  results.append(make('p', 'Searching PubMed…'));
  const papers = await api('/pubmed/search?q=' + encodeURIComponent(q));
  results.replaceChildren();
  if (!papers.length) results.append(make('p', 'No results found.'));
  for (const paper of papers) {
    const card = make('div', undefined, 'source-card');
    const link = make('a', paper.title || `PMID ${paper.pmid}`);
    link.href = paper.url;
    link.target = '_blank';
    link.rel = 'noopener';
    card.append(link, make('p', `${paper.authors} · ${paper.journal} · ${paper.date} · PMID ${paper.pmid}${paper.doi ? ' · DOI ' + paper.doi : ''}`));
    results.append(card);
  }
}

async function loadNotes() {
  const notes = await api('/notes');
  const list = $('notesList');
  list.replaceChildren();
  if (!notes.length) list.append(make('p', 'No notes yet. Create your first note.'));
  for (const note of notes) {
    const card = make('button', undefined, 'note-card');
    card.type = 'button';
    card.append(make('h3', note.title), make('p', note.content || 'Empty note'),
      make('small', new Date(note.updated_at).toLocaleDateString('en-US')));
    card.onclick = () => openNote(note);
    list.append(card);
  }
}

function openNote(note = null) {
  state.noteId = note?.id || null;
  $('noteModalTitle').textContent = note ? 'Edit note' : 'New note';
  $('noteTitle').value = note?.title || '';
  $('noteContent').value = note?.content || '';
  $('deleteNote').classList.toggle('hidden', !note);
  $('noteDialog').showModal();
  $('noteTitle').focus();
}

async function saveNote() {
  const data = { title: $('noteTitle').value.trim(), content: $('noteContent').value };
  if (!data.title) return;
  await api(state.noteId ? '/notes/' + state.noteId : '/notes', {
    method: state.noteId ? 'PUT' : 'POST', body: JSON.stringify(data)
  });
  $('noteDialog').close();
  await loadNotes();
}

async function deleteNote() {
  if (!state.noteId || !confirm('Delete this note permanently?')) return;
  await api('/notes/' + state.noteId, { method: 'DELETE' });
  $('noteDialog').close();
  state.noteId = null;
  await loadNotes();
}

async function loadTasks() {
  const tasks = await api('/tasks');
  const list = $('tasksList');
  list.replaceChildren();
  if (!tasks.length) list.append(make('p', 'No tasks yet. Add your first task.'));
  for (const task of tasks) {
    const row = make('div', undefined, 'task-row' + (task.completed ? ' done' : ''));
    const check = make('input');
    check.type = 'checkbox';
    check.checked = Boolean(task.completed);
    check.setAttribute('aria-label', 'Complete ' + task.title);
    check.onchange = wrap(async () => {
      try {
        await api('/tasks/' + task.id, { method: 'PATCH', body: JSON.stringify({ completed: check.checked }) });
        await loadTasks();
      } catch (error) { check.checked = !check.checked; throw error; }
    });
    const del = make('button', '✕');
    del.type = 'button';
    del.title = 'Delete task';
    del.setAttribute('aria-label', 'Delete ' + task.title);
    del.onclick = wrap(async () => {
      await api('/tasks/' + task.id, { method: 'DELETE' });
      await loadTasks();
    });
    row.append(check, make('span', task.title), del);
    list.append(row);
  }
}

async function boot() {
  try {
    const health = await api('/health');
    $('apiStatus').classList.add('ready');
    $('apiLabel').textContent = health.provider === 'ollama' ?
      (health.ai_configured ? `Ollama: ${health.model}` : 'Choose an Ollama model') :
      'Offline search · no API key';
    if (health.public_access) {
      $('publicNotice').classList.remove('hidden');
      $('accessPill').textContent = '● Public link';
    }
  } catch (error) { toast('Cannot connect: ' + error.message); }
  document.querySelectorAll('.views button').forEach(button => button.onclick = () => setView(button.dataset.view));
  $('openLibrary').onclick = () => setView('library');
  $('exploreAnswer').onclick = () => setExploreMode('answer');
  $('exploreSearch').onclick = () => setExploreMode('search');
  $('buildSemanticIndex').onclick = wrap(buildSemanticIndex);
  $('mobileMenu').onclick = () => {
    const open = $('sidebar').classList.toggle('open');
    $('mobileMenu').setAttribute('aria-expanded', String(open));
  };
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeMenu();
  });
  $('newNote').onclick = () => openNote();
  $('closeNote').onclick = $('cancelNote').onclick = () => $('noteDialog').close();
  $('deleteNote').onclick = wrap(deleteNote);
  $('noteForm').onsubmit = event => { event.preventDefault(); wrap(saveNote)(); };
  $('taskForm').onsubmit = event => {
    event.preventDefault();
    wrap(async () => {
      const title = $('taskTitle').value.trim();
      if (!title) return;
      await api('/tasks', { method: 'POST', body: JSON.stringify({ title }) });
      $('taskTitle').value = '';
      await loadTasks();
    })();
  };
  $('importForm').onsubmit = event => { event.preventDefault(); wrap(importPaper)(); };
  $('answerForm').onsubmit = event => { event.preventDefault(); wrap(answerFromLibrary)(); };
  $('searchForm').onsubmit = event => { event.preventDefault(); wrap(searchLibrary)(); };
  $('pubmedForm').onsubmit = event => { event.preventDefault(); wrap(searchPubMed)(); };
  wrap(refreshSemanticStatus)();
}

boot();
