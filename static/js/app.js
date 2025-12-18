/**
 * Video Analyzer Web Application
 * Main JavaScript file
 */

// ============== State ==============

const state = {
    provider: 'gemini',
    model: '',
    videoFile: null,
    uploadedFileInfo: null,
    videoType: '',
    currentPrompt: '',
    originalPrompt: '',  // Track original prompt to detect edits
    isCustomPrompt: false,
    jobId: null,
    config: null,
    analysisResult: '',  // Store raw markdown result
    // Timer state
    timerInterval: null,
    timerStartTime: null,
    elapsedTime: 0
};

// ============== DOM Elements ==============

const elements = {
    // Provider
    providerRadios: document.querySelectorAll('input[name="provider"]'),
    geminiStatus: document.getElementById('gemini-status'),
    openrouterStatus: document.getElementById('openrouter-status'),

    // API Key
    apiKeySection: document.getElementById('api-key-section'),
    apiKeyInput: document.getElementById('api-key-input'),
    saveApiKeyBtn: document.getElementById('save-api-key-btn'),
    providerName: document.getElementById('provider-name'),
    apiKeyHint: document.getElementById('api-key-hint'),

    // Reset Key
    resetKeySection: document.getElementById('reset-key-section'),
    configuredProviderName: document.getElementById('configured-provider-name'),
    changeKeyBtn: document.getElementById('change-key-btn'),
    resetKeyBtn: document.getElementById('reset-key-btn'),

    // Model
    modelSelect: document.getElementById('model-select'),

    // Upload
    uploadArea: document.getElementById('upload-area'),
    videoInput: document.getElementById('video-input'),
    videoInfo: document.getElementById('video-info'),
    videoPreview: document.getElementById('video-preview'),
    videoName: document.getElementById('video-name'),
    videoDuration: document.getElementById('video-duration'),
    videoResolution: document.getElementById('video-resolution'),
    videoSize: document.getElementById('video-size'),
    changeVideoBtn: document.getElementById('change-video-btn'),

    // Options
    videoTypeSelect: document.getElementById('video-type-select'),
    videoTypeDescription: document.getElementById('video-type-description'),
    promptTextarea: document.getElementById('prompt-textarea'),
    withKeyframes: document.getElementById('with-keyframes'),

    // Analyze
    analyzeBtn: document.getElementById('analyze-btn'),

    // Progress
    progressSection: document.getElementById('progress-section'),
    progressFill: document.getElementById('progress-fill'),
    progressText: document.getElementById('progress-text'),
    progressPercent: document.getElementById('progress-percent'),
    progressTimer: document.getElementById('progress-timer'),

    // Results
    resultsSection: document.getElementById('results-section'),
    resultsContent: document.getElementById('results-content'),
    resultsTime: document.getElementById('results-time'),
    copyResultsBtn: document.getElementById('copy-results-btn'),
    downloadResultsBtn: document.getElementById('download-results-btn'),
    newAnalysisBtn: document.getElementById('new-analysis-btn'),

    // Error
    errorSection: document.getElementById('error-section'),
    errorMessage: document.getElementById('error-message'),
    retryBtn: document.getElementById('retry-btn'),

    // Toast
    toastContainer: document.getElementById('toast-container')
};

// ============== API Functions ==============

async function fetchConfig() {
    try {
        const response = await fetch('/api/config');
        state.config = await response.json();
        updateProviderStatus();
    } catch (error) {
        console.error('Failed to fetch config:', error);
        showToast('Failed to load configuration', 'error');
    }
}

async function fetchVideoTypes() {
    try {
        const response = await fetch('/api/video-types');
        const types = await response.json();

        // Build options with Custom at the end
        let optionsHtml = types.map(type =>
            `<option value="${type.id}" ${!type.available ? 'disabled' : ''}>${type.name}</option>`
        ).join('');

        // Add Custom option (hidden initially, shown when user edits prompt)
        optionsHtml += '<option value="custom" class="custom-option" style="display: none;">Custom</option>';

        elements.videoTypeSelect.innerHTML = optionsHtml;

        // Select first available type
        const firstAvailable = types.find(t => t.available);
        if (firstAvailable) {
            elements.videoTypeSelect.value = firstAvailable.id;
            state.videoType = firstAvailable.id;
            loadPrompt(firstAvailable.id);
        }
    } catch (error) {
        console.error('Failed to fetch video types:', error);
        elements.videoTypeSelect.innerHTML = '<option value="">Failed to load types</option>';
    }
}

async function fetchModels(provider) {
    try {
        elements.modelSelect.innerHTML = '<option value="">Loading video-capable models...</option>';

        const response = await fetch(`/api/models/${provider}`);
        const data = await response.json();

        if (data.models && data.models.length > 0) {
            elements.modelSelect.innerHTML = data.models.map(model => {
                // Build display label with useful info
                let label = model.name;

                // Add context length if available
                if (model.context_length) {
                    const contextK = Math.round(model.context_length / 1000);
                    label += ` (${contextK}K ctx)`;
                }

                // Add pricing for OpenRouter models
                if (model.pricing && typeof model.pricing === 'string') {
                    label += ` - ${model.pricing}`;
                }

                // Add input token limit for Gemini
                if (model.input_token_limit) {
                    const limitM = (model.input_token_limit / 1000000).toFixed(1);
                    if (limitM >= 1) {
                        label += ` (${limitM}M tokens)`;
                    }
                }

                return `<option value="${model.id}" title="${model.description || ''}">${label}</option>`;
            }).join('');

            // Select default model
            const defaultModel = provider === 'gemini'
                ? state.config?.default_gemini_model
                : state.config?.default_openrouter_model;

            if (defaultModel && data.models.some(m => m.id === defaultModel)) {
                elements.modelSelect.value = defaultModel;
            }

            state.model = elements.modelSelect.value;

            // Show model count
            const modelCount = data.models.length;
            showToast(`Found ${modelCount} video-capable model${modelCount > 1 ? 's' : ''}`, 'success');
        } else {
            elements.modelSelect.innerHTML = '<option value="">No video-capable models found</option>';
            showToast('No video-capable models available for this provider', 'warning');
        }

        if (!data.from_api) {
            showToast('Using default model list (enter API key for full list)', 'warning');
        }
    } catch (error) {
        console.error('Failed to fetch models:', error);
        elements.modelSelect.innerHTML = '<option value="">Failed to load models</option>';
        showToast('Failed to fetch models from API', 'error');
    }
}

async function loadPrompt(videoType) {
    try {
        const withKeyframes = elements.withKeyframes.checked;
        const response = await fetch(`/api/prompt/${videoType}?with_keyframes=${withKeyframes}`);
        const data = await response.json();

        state.currentPrompt = data.prompt;
        state.originalPrompt = data.prompt;  // Store original for comparison
        state.isCustomPrompt = false;

        // Load full prompt into editable textarea
        elements.promptTextarea.value = data.prompt;
        elements.videoTypeDescription.textContent = data.type_info?.description || '';

        // Hide Custom option and show regular selection
        const customOption = elements.videoTypeSelect.querySelector('option[value="custom"]');
        if (customOption) {
            customOption.style.display = 'none';
        }
    } catch (error) {
        console.error('Failed to load prompt:', error);
        elements.promptTextarea.value = '';
        elements.promptTextarea.placeholder = 'Failed to load prompt';
    }
}

async function setApiKey(provider, apiKey) {
    try {
        const btn = elements.saveApiKeyBtn;
        btn.querySelector('.btn-text').style.display = 'none';
        btn.querySelector('.btn-loading').style.display = 'inline-flex';
        btn.disabled = true;

        const response = await fetch('/api/set-api-key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider, api_key: apiKey })
        });

        const data = await response.json();

        if (data.success) {
            showToast(`${provider} API key saved successfully`, 'success');

            // Update config
            if (provider === 'gemini') {
                state.config.gemini_configured = true;
            } else {
                state.config.openrouter_configured = true;
            }

            updateProviderStatus();
            elements.apiKeyInput.value = '';

            // Refresh models
            fetchModels(provider);
        } else {
            showToast(data.error || 'Failed to save API key', 'error');
        }
    } catch (error) {
        console.error('Failed to set API key:', error);
        showToast('Failed to save API key', 'error');
    } finally {
        const btn = elements.saveApiKeyBtn;
        btn.querySelector('.btn-text').style.display = 'inline';
        btn.querySelector('.btn-loading').style.display = 'none';
        btn.disabled = false;
    }
}

async function uploadVideo(file) {
    try {
        const formData = new FormData();
        formData.append('file', file);

        const response = await fetch('/api/upload', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Upload failed');
        }

        const data = await response.json();
        state.uploadedFileInfo = data;

        showVideoInfo(file, data);
        showToast('Video uploaded successfully', 'success');
        updateAnalyzeButton();
    } catch (error) {
        console.error('Upload failed:', error);
        showToast(error.message || 'Failed to upload video', 'error');
    }
}

async function startAnalysis() {
    try {
        if (!state.uploadedFileInfo) {
            showToast('Please upload a video first', 'error');
            return;
        }

        const formData = new FormData();
        formData.append('file_id', state.uploadedFileInfo.file_id);
        formData.append('filename', state.uploadedFileInfo.filename);
        formData.append('video_type', state.videoType === 'custom' ? 'generic' : state.videoType);
        formData.append('provider', state.provider);
        formData.append('model', state.model);
        formData.append('with_keyframes', elements.withKeyframes.checked);

        // Get prompt from textarea (either original or edited)
        const currentPrompt = elements.promptTextarea.value.trim();
        if (currentPrompt) {
            formData.append('custom_prompt', currentPrompt);
        }

        const response = await fetch('/api/analyze', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Failed to start analysis');
        }

        const data = await response.json();
        state.jobId = data.job_id;

        // Show progress
        elements.analyzeBtn.disabled = true;
        elements.progressSection.style.display = 'block';
        elements.resultsSection.style.display = 'none';
        elements.errorSection.style.display = 'none';

        // Start timer
        startTimer();

        // Start polling
        pollJobStatus();
    } catch (error) {
        console.error('Failed to start analysis:', error);
        showToast(error.message || 'Failed to start analysis', 'error');
    }
}

async function pollJobStatus() {
    if (!state.jobId) return;

    try {
        const response = await fetch(`/api/job/${state.jobId}`);
        const job = await response.json();

        // Update progress
        elements.progressFill.style.width = `${job.progress}%`;
        elements.progressText.textContent = job.current_step;
        elements.progressPercent.textContent = `${job.progress}%`;

        if (job.status === 'completed') {
            showResults(job.result);
        } else if (job.status === 'failed') {
            showError(job.error);
        } else {
            // Continue polling
            setTimeout(pollJobStatus, 1000);
        }
    } catch (error) {
        console.error('Failed to fetch job status:', error);
        showError('Lost connection to server');
    }
}

// ============== UI Functions ==============

function updateProviderStatus() {
    if (!state.config) return;

    const providerDisplayName = state.provider === 'gemini' ? 'Gemini' : 'OpenRouter';

    // Update status badges
    if (state.config.gemini_configured) {
        elements.geminiStatus.textContent = 'Configured';
        elements.geminiStatus.classList.add('configured');
    } else {
        elements.geminiStatus.textContent = 'Not configured';
        elements.geminiStatus.classList.remove('configured');
    }

    if (state.config.openrouter_configured) {
        elements.openrouterStatus.textContent = 'Configured';
        elements.openrouterStatus.classList.add('configured');
    } else {
        elements.openrouterStatus.textContent = 'Not configured';
        elements.openrouterStatus.classList.remove('configured');
    }

    // Check if current provider is configured
    const isConfigured = state.provider === 'gemini'
        ? state.config.gemini_configured
        : state.config.openrouter_configured;

    if (!isConfigured) {
        // Show API key input section
        elements.apiKeySection.style.display = 'block';
        elements.resetKeySection.style.display = 'none';
        elements.providerName.textContent = providerDisplayName;

        if (state.provider === 'gemini') {
            elements.apiKeyHint.innerHTML = 'Get your Gemini API key from <a href="https://aistudio.google.com/apikey" target="_blank">Google AI Studio</a>';
        } else {
            elements.apiKeyHint.innerHTML = 'Get your OpenRouter API key from <a href="https://openrouter.ai/keys" target="_blank">OpenRouter</a>';
        }
    } else {
        // Show reset key section
        elements.apiKeySection.style.display = 'none';
        elements.resetKeySection.style.display = 'block';
        elements.configuredProviderName.textContent = providerDisplayName;
    }

    updateAnalyzeButton();
}

async function resetApiKey(provider) {
    try {
        const response = await fetch('/api/reset-api-key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider })
        });

        const data = await response.json();

        if (data.success) {
            showToast(`${provider} API key has been reset`, 'success');

            // Update config
            if (provider === 'gemini') {
                state.config.gemini_configured = false;
            } else {
                state.config.openrouter_configured = false;
            }

            updateProviderStatus();
            fetchModels(state.provider);
        } else {
            showToast(data.error || 'Failed to reset API key', 'error');
        }
    } catch (error) {
        console.error('Failed to reset API key:', error);
        showToast('Failed to reset API key', 'error');
    }
}

function showChangeKeyInput() {
    // Hide reset section, show input section
    elements.resetKeySection.style.display = 'none';
    elements.apiKeySection.style.display = 'block';
    elements.apiKeyInput.value = '';
    elements.apiKeyInput.focus();
}

function showVideoInfo(file, uploadInfo) {
    elements.uploadArea.style.display = 'none';
    elements.videoInfo.style.display = 'grid';

    // Create object URL for preview
    const objectUrl = URL.createObjectURL(file);
    elements.videoPreview.src = objectUrl;

    // Show info
    elements.videoName.textContent = uploadInfo.original_name;
    elements.videoDuration.textContent = formatDuration(uploadInfo.video_info.duration);
    elements.videoResolution.textContent = `${uploadInfo.video_info.width}x${uploadInfo.video_info.height}`;
    elements.videoSize.textContent = formatFileSize(uploadInfo.size_bytes);
}

function hideVideoInfo() {
    elements.uploadArea.style.display = 'block';
    elements.videoInfo.style.display = 'none';
    elements.videoPreview.src = '';
    state.videoFile = null;
    state.uploadedFileInfo = null;
    updateAnalyzeButton();
}

function showResults(result) {
    // Stop timer and show elapsed time
    stopTimer();
    const elapsedFormatted = formatTimer(state.elapsedTime);
    elements.resultsTime.textContent = `(${elapsedFormatted})`;

    elements.progressSection.style.display = 'none';
    elements.resultsSection.style.display = 'block';
    elements.analyzeBtn.disabled = false;

    // Store raw markdown for copy/download
    state.analysisResult = result;

    // Render markdown
    if (typeof marked !== 'undefined') {
        elements.resultsContent.innerHTML = marked.parse(result);
    } else {
        elements.resultsContent.textContent = result;
    }

    // Scroll to results
    elements.resultsSection.scrollIntoView({ behavior: 'smooth' });
}

function showError(error) {
    // Stop timer on error
    stopTimer();

    elements.progressSection.style.display = 'none';
    elements.errorSection.style.display = 'block';
    elements.errorMessage.textContent = error;
    elements.analyzeBtn.disabled = false;
}

function updateAnalyzeButton() {
    const hasVideo = state.uploadedFileInfo !== null;
    const hasProvider = state.provider !== '';
    const hasModel = state.model !== '';
    const hasVideoType = state.videoType !== '';
    const isConfigured = state.provider === 'gemini'
        ? state.config?.gemini_configured
        : state.config?.openrouter_configured;

    elements.analyzeBtn.disabled = !(hasVideo && hasProvider && hasModel && hasVideoType && isConfigured);
}

function showToast(message, type = 'success') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;

    elements.toastContainer.appendChild(toast);

    setTimeout(() => {
        toast.remove();
    }, 5000);
}

function resetAnalysis() {
    elements.progressSection.style.display = 'none';
    elements.resultsSection.style.display = 'none';
    elements.errorSection.style.display = 'none';
    state.jobId = null;
}

// ============== Utility Functions ==============

function formatDuration(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);

    if (h > 0) {
        return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    }
    return `${m}:${s.toString().padStart(2, '0')}`;
}

function formatTimer(ms) {
    const totalSeconds = Math.floor(ms / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
}

function startTimer() {
    state.timerStartTime = Date.now();
    state.elapsedTime = 0;
    elements.progressTimer.textContent = '00:00';

    state.timerInterval = setInterval(() => {
        state.elapsedTime = Date.now() - state.timerStartTime;
        elements.progressTimer.textContent = formatTimer(state.elapsedTime);
    }, 1000);
}

function stopTimer() {
    if (state.timerInterval) {
        clearInterval(state.timerInterval);
        state.timerInterval = null;
    }
    // Final update to get exact time
    if (state.timerStartTime) {
        state.elapsedTime = Date.now() - state.timerStartTime;
    }
}

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
}

// ============== Event Listeners ==============

// Provider selection
elements.providerRadios.forEach(radio => {
    radio.addEventListener('change', (e) => {
        state.provider = e.target.value;
        updateProviderStatus();
        fetchModels(state.provider);
    });
});

// Model selection
elements.modelSelect.addEventListener('change', (e) => {
    state.model = e.target.value;
    updateAnalyzeButton();
});

// API Key save
elements.saveApiKeyBtn.addEventListener('click', () => {
    const apiKey = elements.apiKeyInput.value.trim();
    if (apiKey) {
        setApiKey(state.provider, apiKey);
    } else {
        showToast('Please enter an API key', 'error');
    }
});

// Change API Key
elements.changeKeyBtn.addEventListener('click', () => {
    showChangeKeyInput();
});

// Reset API Key
elements.resetKeyBtn.addEventListener('click', () => {
    if (confirm(`Are you sure you want to reset the ${state.provider === 'gemini' ? 'Gemini' : 'OpenRouter'} API key?`)) {
        resetApiKey(state.provider);
    }
});

// Video upload
elements.uploadArea.addEventListener('click', () => {
    elements.videoInput.click();
});

elements.uploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    elements.uploadArea.classList.add('dragover');
});

elements.uploadArea.addEventListener('dragleave', () => {
    elements.uploadArea.classList.remove('dragover');
});

elements.uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    elements.uploadArea.classList.remove('dragover');

    const files = e.dataTransfer.files;
    if (files.length > 0) {
        state.videoFile = files[0];
        uploadVideo(files[0]);
    }
});

elements.videoInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
        state.videoFile = e.target.files[0];
        uploadVideo(e.target.files[0]);
    }
});

elements.changeVideoBtn.addEventListener('click', hideVideoInfo);

// Video type selection
elements.videoTypeSelect.addEventListener('change', (e) => {
    state.videoType = e.target.value;
    loadPrompt(e.target.value);
    updateAnalyzeButton();
});

// Keyframes toggle
elements.withKeyframes.addEventListener('change', () => {
    if (state.videoType && state.videoType !== 'custom') {
        loadPrompt(state.videoType);
    }
});

// Prompt textarea - detect edits and switch to Custom
elements.promptTextarea.addEventListener('input', () => {
    const currentValue = elements.promptTextarea.value;

    // Check if prompt has been modified from original
    if (currentValue !== state.originalPrompt && !state.isCustomPrompt) {
        state.isCustomPrompt = true;

        // Show and select Custom option
        const customOption = elements.videoTypeSelect.querySelector('option[value="custom"]');
        if (customOption) {
            customOption.style.display = '';
            elements.videoTypeSelect.value = 'custom';
            state.videoType = 'custom';
            elements.videoTypeDescription.textContent = 'Using customized prompt';
        }
    } else if (currentValue === state.originalPrompt && state.isCustomPrompt) {
        // User reverted to original prompt
        state.isCustomPrompt = false;

        // Hide Custom option and restore original selection
        const customOption = elements.videoTypeSelect.querySelector('option[value="custom"]');
        if (customOption) {
            customOption.style.display = 'none';
        }

        // Find the original video type and select it
        const types = elements.videoTypeSelect.querySelectorAll('option:not([value="custom"])');
        for (const option of types) {
            if (!option.disabled && option.value) {
                elements.videoTypeSelect.value = option.value;
                state.videoType = option.value;
                loadPrompt(option.value);
                break;
            }
        }
    }

    state.currentPrompt = currentValue;
});

// Analyze button
elements.analyzeBtn.addEventListener('click', startAnalysis);

// Copy results (as markdown)
elements.copyResultsBtn.addEventListener('click', () => {
    navigator.clipboard.writeText(state.analysisResult).then(() => {
        showToast('Results copied as Markdown', 'success');
    });
});

// Download results (as markdown)
elements.downloadResultsBtn.addEventListener('click', () => {
    const blob = new Blob([state.analysisResult], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `analysis_${new Date().toISOString().slice(0, 10)}.md`;
    a.click();
    URL.revokeObjectURL(url);
});

// New analysis
elements.newAnalysisBtn.addEventListener('click', () => {
    resetAnalysis();
    hideVideoInfo();
    // Reload current prompt to reset any edits
    if (state.videoType && state.videoType !== 'custom') {
        loadPrompt(state.videoType);
    }
});

// Retry
elements.retryBtn.addEventListener('click', () => {
    resetAnalysis();
    startAnalysis();
});

// ============== Initialize ==============

async function init() {
    await fetchConfig();
    await fetchVideoTypes();
    fetchModels(state.provider);
}

// Start app
init();
