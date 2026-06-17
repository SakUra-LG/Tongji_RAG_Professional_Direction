// API基础配置
const BASE_URL = import.meta.env.VITE_API_BASE_URL || '';
const API_PREFIX = import.meta.env.VITE_API_PREFIX || '/api/v1';

// Token管理
const getAccessToken = () => localStorage.getItem('access_token');
const getRefreshToken = () => localStorage.getItem('refresh_token');
const setTokens = (accessToken, refreshToken) => {
  localStorage.setItem('access_token', accessToken);
  if (refreshToken) {
    localStorage.setItem('refresh_token', refreshToken);
  }
};
const clearTokens = () => {
  localStorage.removeItem('access_token');
  localStorage.removeItem('refresh_token');
  localStorage.removeItem('user_info');
};

// 通用请求函数
async function request(url, options = {}) {
  const { skipAuthRefresh = false, auth = true, ...fetchOptions } = options;
  const token = getAccessToken();
  const headers = {
    'Content-Type': 'application/json',
    ...fetchOptions.headers,
  };
  
  if (auth && token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  try {
    const response = await fetch(`${BASE_URL}${API_PREFIX}${url}`, {
      ...fetchOptions,
      headers,
    });

    // 处理401错误 - Token过期
    if (response.status === 401) {
      if (skipAuthRefresh) {
        const errorData = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(errorData.detail || `请求失败: ${response.status}`);
      }
      const refreshToken = getRefreshToken();
      if (refreshToken) {
        try {
          const refreshResponse = await refreshTokenAPI(refreshToken);
          if (refreshResponse.access_token) {
            setTokens(refreshResponse.access_token, refreshResponse.refresh_token);
            // 重试原请求
            headers['Authorization'] = `Bearer ${refreshResponse.access_token}`;
            return fetch(`${BASE_URL}${API_PREFIX}${url}`, {
              ...fetchOptions,
              headers,
            }).then(res => res.json());
          }
        } catch (error) {
          clearTokens();
          throw new Error('Token已过期，请重新登录');
        }
      } else {
        clearTokens();
        throw new Error('未授权，请重新登录');
      }
    }

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(errorData.detail || `请求失败: ${response.status}`);
    }

    return await response.json();
  } catch (error) {
    if (error.message.includes('fetch')) {
      throw new Error('网络错误，请检查连接');
    }
    throw error;
  }
}

// 认证API
export const authAPI = {
  // 用户登录
  async login(username, password) {
    const response = await request('/login', {
      method: 'POST',
      auth: false,
      skipAuthRefresh: true,
      body: JSON.stringify({ username, password }),
    });
    setTokens(response.access_token, response.refresh_token);
    if (response.user_info) {
      localStorage.setItem('user_info', JSON.stringify(response.user_info));
    }
    return response;
  },

  // 访客登录
  async guestLogin() {
    const response = await request('/guest-login', {
      method: 'POST',
      body: JSON.stringify({}),
    });
    setTokens(response.access_token, response.refresh_token);
    if (response.user_info) {
      localStorage.setItem('user_info', JSON.stringify(response.user_info));
    }
    return response;
  },

  // 刷新Token
  async refresh(refreshToken) {
    const response = await request('/refresh', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    setTokens(response.access_token, response.refresh_token);
    if (response.user_info) {
      localStorage.setItem('user_info', JSON.stringify(response.user_info));
    }
    return response;
  },

  // 退出登录
  async logout() {
    const refreshToken = getRefreshToken();
    if (refreshToken) {
      try {
        await request('/logout', {
          method: 'POST',
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
      } catch (error) {
        console.error('Logout error:', error);
      }
    }
    clearTokens();
  },
};

// 会话管理API
export const sessionAPI = {
  // 创建新会话
  async createSession(type) {
    return await request('/session/new', {
      method: 'POST',
      body: JSON.stringify({ type }),
    });
  },

  // 获取会话列表
  async getSessionList(type) {
    return await request(`/session/list?type=${type}`, {
      method: 'GET',
    });
  },

  // 获取会话历史
  async getSessionHistory(sessionId) {
    return await request(`/session/${sessionId}/history`, {
      method: 'GET',
    });
  },

  // 删除会话
  async deleteSession(sessionId) {
    return await request(`/session/${sessionId}`, {
      method: 'DELETE',
    });
  },
};

export const profileAPI = {
  async getMyProfile() {
    return await request('/me/profile', { method: 'GET' });
  },

  async getNotices() {
    return await request('/notices', { method: 'GET' });
  },
};

// 聊天API
export const chatAPI = {
  // 发送消息（SSE流式响应）
  async sendMessage(type, query, sessionId, onChunk, onError, onComplete, onMetadata) {
    const token = getAccessToken();
    const headers = {
      'Content-Type': 'application/json',
    };
    
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    try {
      const response = await fetch(`${BASE_URL}${API_PREFIX}/chat/${type}`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          query,
          session_id: sessionId,
          stream: true,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(errorData.detail || `请求失败: ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        
        if (done) {
          if (onComplete) onComplete();
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.trim() === '') continue;
          
          if (line.startsWith('data: ')) {
            const data = line.slice(6);
            
            if (data === '[DONE]') {
              if (onComplete) onComplete();
              return;
            }

            let json;
            try {
              json = JSON.parse(data);
            } catch (e) {
              console.error('Parse SSE data error:', e, data);
              continue;
            }
            if (json.error) {
              throw new Error(json.error);
            }
            if (json.metadata && onMetadata) {
              onMetadata(json.metadata);
              continue;
            }
            if (json.chunk && onChunk) {
              onChunk(json.chunk);
            }
          }
        }
      }
    } catch (error) {
      if (onError) {
        onError(error);
      } else {
        throw error;
      }
    }
  },
};

export const adminAPI = {
  async listFaqs() {
    return await request('/admin/faqs', { method: 'GET' });
  },

  async createFaq(payload) {
    return await request('/admin/faqs', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },

  async updateFaq(id, payload) {
    return await request(`/admin/faqs/${id}`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    });
  },

  async listKnowledge(collection = '') {
    const suffix = collection ? `?collection=${encodeURIComponent(collection)}` : '';
    return await request(`/admin/knowledge${suffix}`, { method: 'GET' });
  },

  async crawlPreview(url, maxPages = 8) {
    return await request('/admin/crawl/preview', {
      method: 'POST',
      body: JSON.stringify({ url, max_pages: maxPages }),
    });
  },

  async saveCrawl(sourceUrl, accessScope, blocks) {
    return await request('/admin/crawl/save', {
      method: 'POST',
      body: JSON.stringify({
        source_url: sourceUrl,
        access_scope: accessScope,
        blocks,
      }),
    });
  },
};

// 辅助函数：刷新Token（内部使用）
async function refreshTokenAPI(refreshToken) {
  const response = await fetch(`${BASE_URL}${API_PREFIX}/refresh`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });

  if (!response.ok) {
    throw new Error('Token刷新失败');
  }

  return await response.json();
}

// 获取用户信息
export const getUserInfo = () => {
  const userInfoStr = localStorage.getItem('user_info');
  return userInfoStr ? JSON.parse(userInfoStr) : null;
};

// 检查是否已登录
export const isAuthenticated = () => {
  return !!getAccessToken();
};

