<script setup>
import { ref, onMounted, computed } from 'vue';
import {
  Send,
  User,
  BookOpen,
  Calendar,
  Clock,
  Shield,
  Search,
  Menu,
  Database,
  X,
  Sparkles,
  Globe,
  Map,
  FileText,
  AlertCircle,
  LogIn,
  ChevronRight
} from 'lucide-vue-next';
import SynapseQShell from './components/SynapseQShell.vue';
import AdminPanel from './components/AdminPanel.vue';
import { authAPI, sessionAPI, chatAPI, getUserInfo, isAuthenticated } from './api/api.js';

const showLogin = ref(true);
const loggingIn = ref(false);
const loginError = ref('');
const loginForm = ref({ email: '', password: '' });
const rememberLogin = ref(false);

const currentMode = ref('visitor'); // visitor | student | denied
const currentUserInfo = ref(null); // 存储当前登录用户信息
const showAdminPanel = ref(false);

// 单一自动问答入口，后端根据身份决定可检索的数据范围
const currentType = ref('auto');
const typeConfig = {
  auto: {
    label: '智能问答',
    description: '自动检索授权数据',
    icon: Sparkles,
    color: 'blue',
    badge: '身份检索已连接',
    title: 'SynapseQ 智能问答'
  },
  public: {
    label: '公开',
    description: '校务公开/FAQ',
    icon: Globe,
    color: 'green',
    badge: 'Standard 库已连接',
    title: '访客通道 (Public Access)'
  },
  academic: {
    label: '学术',
    description: '文献/知识库',
    icon: BookOpen,
    color: 'purple',
    badge: 'Knowledge 库已连接',
    title: '学术科研模式 (Academic Mode)'
  },
  internal: {
    label: '内部',
    description: '通知/办事',
    icon: FileText,
    color: 'blue',
    badge: 'Internal 库已连接',
    title: '校内服务通道 (Campus Services)'
  },
  personal: {
    label: '个人',
    description: '画像/推荐',
    icon: User,
    color: 'orange',
    badge: 'Personal 库已连接',
    title: '个人服务 (Personal Services)'
  }
};

// 根据用户角色获取可用的type列表
const getAvailableTypes = (role) => {
  return ['auto'];
};

// 统一的会话和消息存储（按type管理）
const typeSessions = ref({
  auto: { conversations: [], currentId: '', messages: [] },
  public: { conversations: [], currentId: '', messages: [] },
  academic: { conversations: [], currentId: '', messages: [] },
  internal: { conversations: [], currentId: '', messages: [] },
  personal: { conversations: [], currentId: '', messages: [] }
});

// 当前type的会话和消息（计算属性）
const currentConversations = computed(() => {
  const type = currentType.value;
  if (!typeSessions.value[type]) {
    typeSessions.value[type] = { conversations: [], currentId: '', messages: [] };
  }
  return typeSessions.value[type]?.conversations || [];
});

const currentMessages = computed(() => {
  const type = currentType.value;
  if (!typeSessions.value[type]) {
    typeSessions.value[type] = { conversations: [], currentId: '', messages: [] };
  }
  return typeSessions.value[type]?.messages || [];
});

const currentConversationId = computed({
  get: () => {
    const type = currentType.value;
    if (!typeSessions.value[type]) {
      typeSessions.value[type] = { conversations: [], currentId: '', messages: [] };
    }
    return typeSessions.value[type]?.currentId || '';
  },
  set: (val) => {
    const type = currentType.value;
    if (!typeSessions.value[type]) {
      typeSessions.value[type] = { conversations: [], currentId: '', messages: [] };
    }
    if (typeSessions.value[type]) {
      typeSessions.value[type].currentId = val;
    }
  }
});

// 当前type的配置
const currentTypeConfig = computed(() => {
  return typeConfig[currentType.value] || typeConfig.public;
});

const isAdminUser = computed(() => {
  const rawRole = (currentUserInfo.value?.role || '').toLowerCase();
  const displayRole = (currentUser.value?.role || '').toLowerCase();
  return rawRole.includes('admin') || displayRole.includes('admin') || currentMode.value === 'admin';
});

// 辅助函数：获取示例问题
const getExampleQuestions = (type) => {
  if (type === 'auto') {
    if (currentMode.value === 'student') {
      return ['我的绩点是多少？', '查看我的课程表', '我的考试信息', '最近有什么校内通知？'];
    }
    return ['同济大学的校训是什么？', '同济大学创建于哪一年？', '嘉定校区地址在哪里？', '介绍一下同济大学'];
  }
  const questions = {
    public: ['嘉定校区图书馆在哪里？', '四平路校区地图', '校车时刻表(仅公开版)', '2025本科招生简章'],
    academic: ['汽车学院在自动驾驶领域最近有什么发表？', '查找IEEE关于机器学习的论文', 'CNKI中关于人工智能的最新研究', '同济大学2024年科研年报'],
    internal: ['嘉定校区图书馆几点关门？', '查看我今天有什么课程', '我的考试信息', '校园卡余额查询'],
    personal: ['我的选课信息', '个人成绩单', '我的课程表', '我的考试信息']
  };
  return questions[type] || questions.public;
};

// 辅助函数：获取热门问题
const getPopularQuestions = (type) => {
  const questions = {
    public: ['同济大学的校训是什么？', '同济大学创建于哪一年？', '同济大学是"985""211"高校吗？', '同济大学的土木工程在全国处于什么水平？'],
    academic: [],
    internal: [],
    personal: []
  };
  return questions[type] || [];
};

// 辅助函数：获取欢迎标题
const getWelcomeTitle = (type) => {
  if (type === 'auto' && currentMode.value === 'student') {
    return `欢迎回来，${currentUser.value.name}！`;
  }
  if (type === 'auto') return '欢迎使用 SynapseQ';
  return `欢迎使用 SynapseQ ${typeConfig[type]?.label || ''}`;
};

// 辅助函数：获取连接的集合
const getConnectedCollections = (type) => {
  const collections = {
    public: [{ name: 'Standard (公开)', color: 'green', access: 'Read' }],
    academic: [
      { name: 'Standard (公开)', color: 'green', access: 'Read' },
      { name: 'Knowledge (学术)', color: 'purple', access: 'Read' }
    ],
    internal: [
      { name: 'Standard (公开)', color: 'green', access: 'Read' },
      { name: 'Internal (内部)', color: 'blue', access: 'Read' },
      { name: 'Person_info (个人)', color: 'orange', access: 'Private' }
    ],
    personal: [
      { name: 'Standard (公开)', color: 'green', access: 'Read' },
      { name: 'Person_info (个人)', color: 'orange', access: 'Private' }
    ]
  };
  return collections[type] || collections.public;
};

// 获取type按钮的class
const getTypeButtonClass = (type) => {
  const baseClass = 'px-4 py-2 rounded-lg text-sm font-medium transition-all flex items-center gap-2 flex-shrink-0';
  if (currentType.value === type) {
    const color = typeConfig[type].color;
    const colorClasses = {
      green: 'bg-green-100 text-green-700 border-2 border-green-300 shadow-sm',
      purple: 'bg-purple-100 text-purple-700 border-2 border-purple-300 shadow-sm',
      blue: 'bg-blue-100 text-blue-700 border-2 border-blue-300 shadow-sm',
      orange: 'bg-orange-100 text-orange-700 border-2 border-orange-300 shadow-sm'
    };
    return `${baseClass} ${colorClasses[color] || colorClasses.blue}`;
  }
  return `${baseClass} bg-gray-50 text-gray-600 hover:bg-gray-100 border-2 border-transparent`;
};

// 获取右侧面板渐变背景class
const getRightPanelGradientClass = () => {
  const color = currentTypeConfig.value.color;
  const gradients = {
    green: 'bg-gradient-to-br from-green-500 to-emerald-600',
    purple: 'bg-gradient-to-br from-purple-500 to-pink-600',
    blue: 'bg-gradient-to-br from-blue-500 to-indigo-600',
    orange: 'bg-gradient-to-br from-orange-500 to-amber-600'
  };
  return `${gradients[color] || gradients.blue} rounded-xl p-4 text-white shadow-lg relative overflow-hidden`;
};

// 获取集合项的class
const getCollectionItemClass = (color) => {
  const classes = {
    green: 'flex items-center justify-between p-3 bg-green-50 rounded-lg border border-green-100',
    purple: 'flex items-center justify-between p-3 bg-purple-50 rounded-lg border border-purple-100',
    blue: 'flex items-center justify-between p-3 bg-blue-50 rounded-lg border border-blue-100',
    orange: 'flex items-center justify-between p-3 bg-orange-50 rounded-lg border border-orange-100'
  };
  return classes[color] || classes.green;
};

// 获取集合点的class
const getCollectionDotClass = (color) => {
  const classes = {
    green: 'w-2 h-2 rounded-full bg-green-500',
    purple: 'w-2 h-2 rounded-full bg-purple-500',
    blue: 'w-2 h-2 rounded-full bg-blue-500',
    orange: 'w-2 h-2 rounded-full bg-orange-500'
  };
  return classes[color] || classes.green;
};

// 获取热门问题按钮的class
const getPopularQuestionButtonClass = () => {
  const color = currentTypeConfig.value.color;
  const classes = {
    green: 'w-full p-3 bg-white border border-gray-100 rounded-lg text-xs text-gray-600 hover:border-green-200 hover:text-green-700 cursor-pointer transition-colors flex items-center justify-between group text-left',
    purple: 'w-full p-3 bg-white border border-gray-100 rounded-lg text-xs text-gray-600 hover:border-purple-200 hover:text-purple-700 cursor-pointer transition-colors flex items-center justify-between group text-left',
    blue: 'w-full p-3 bg-white border border-gray-100 rounded-lg text-xs text-gray-600 hover:border-blue-200 hover:text-blue-700 cursor-pointer transition-colors flex items-center justify-between group text-left',
    orange: 'w-full p-3 bg-white border border-gray-100 rounded-lg text-xs text-gray-600 hover:border-orange-200 hover:text-orange-700 cursor-pointer transition-colors flex items-center justify-between group text-left'
  };
  return classes[color] || classes.blue;
};

// 根据用户角色获取信息面板的渐变背景class
const getUserPanelGradientClass = () => {
  if (!currentUserInfo.value) {
    return 'bg-gradient-to-br from-green-500 to-emerald-600 rounded-xl p-4 text-white shadow-lg relative overflow-hidden';
  }
  
  const role = (currentUserInfo.value.role || '').toLowerCase();
  
  if (role.includes('student') || role.includes('teacher')) {
    return 'bg-gradient-to-br from-blue-500 to-indigo-600 rounded-xl p-4 text-white shadow-lg relative overflow-hidden';
  } else {
    return 'bg-gradient-to-br from-green-500 to-emerald-600 rounded-xl p-4 text-white shadow-lg relative overflow-hidden';
  }
};

// 根据用户角色获取连接的集合（而不是根据type）
const getUserConnectedCollections = () => {
  const role = (
    currentUserInfo.value?.role
    || currentUser.value?.role
    || ''
  ).toLowerCase();
  if (!currentUserInfo.value || role.includes('guest')) {
    return [{ name: 'Standard (公开)', color: 'green', access: 'Read' }];
  }
  if (isAdminUser.value) {
    return [
      { name: 'Standard (公开)', color: 'green', access: 'Manage' },
      { name: 'Knowledge (师生)', color: 'blue', access: 'Manage' },
      { name: 'FAQ', color: 'orange', access: 'Edit' }
    ];
  }
  
  const collections = [{ name: 'Standard (公开)', color: 'green', access: 'Read' }];
  
  if (role.includes('student') || role.includes('teacher')) {
    collections.push({ name: 'Internal (校内爬取)', color: 'blue', access: 'Read' });
    collections.push({ name: 'Person_info (个人)', color: 'orange', access: 'Private' });
  }
  
  return collections;
};

// 根据用户角色获取常用服务链接
const getUserCommonServices = () => {
  if (!currentUserInfo.value) {
    return [];
  }
  
  const role = (currentUserInfo.value.role || '').toLowerCase();
  
  if (role.includes('student') || role.includes('teacher')) {
    return [
      { name: '教学信息管理系统', url: 'https://1.tongji.edu.cn/' },
      { name: 'canvas', url: 'https://canvas.tongji.edu.cn/' },
      { name: '同济邮箱', url: 'https://mail.tongji.edu.cn/' }
    ];
  }
  
  return [];
};

// 根据用户角色获取服务链接的样式class
const getServiceLinkClass = () => {
  if (!currentUserInfo.value) {
    return 'w-full p-3 bg-white border border-gray-100 rounded-lg text-xs text-gray-600 cursor-pointer transition-colors flex items-center justify-between group text-left hover:bg-gray-50';
  }
  
  const role = (currentUserInfo.value.role || '').toLowerCase();
  
  if (role.includes('student') || role.includes('teacher')) {
    return 'w-full p-3 bg-white border border-blue-200 text-blue-700 rounded-lg text-xs cursor-pointer transition-colors flex items-center justify-between group text-left hover:bg-blue-50 hover:border-blue-300';
  }
  
  return 'w-full p-3 bg-white border border-gray-100 rounded-lg text-xs text-gray-600 cursor-pointer transition-colors flex items-center justify-between group text-left hover:bg-gray-50';
};

// 根据用户角色决定显示模式（保留向后兼容）
const getModeByRole = (role) => {
  const roleLower = (role || '').toLowerCase();
  // 根据角色映射到对应模式
  if (roleLower.includes('guest') || roleLower.includes('visitor')) {
    return 'visitor';
  } else if (roleLower.includes('admin')) {
    return 'admin';
  } else if (roleLower.includes('student') || roleLower.includes('teacher')) {
    return 'student';
  }
  // 默认返回student模式
  return 'student';
};

const formatTime = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
const formatDate = (date = new Date()) => date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });

// ========== 统一的Type会话管理 ==========

// 加载指定type的会话列表
const loadTypeSessions = async (type) => {
  try {
    // 确保type存在
    if (!typeSessions.value[type]) {
      typeSessions.value[type] = { conversations: [], currentId: '', messages: [] };
    }
    
    const response = await sessionAPI.getSessionList(type);
    typeSessions.value[type].conversations = (response.data || []).map(session => ({
      id: session.session_id,
      title: session.title || '新对话',
      time: formatDate(new Date(session.created_at)),
      updatedAt: formatTime(),
      type: session.type || type
    }));
  } catch (error) {
    console.error(`加载${type}会话列表失败:`, error);
    // 确保即使出错也有空数组
    if (!typeSessions.value[type]) {
      typeSessions.value[type] = { conversations: [], currentId: '', messages: [] };
    }
  }
};

// 加载指定type的会话历史
const loadTypeHistory = async (type, sessionId) => {
  try {
    // 确保type存在
    if (!typeSessions.value[type]) {
      typeSessions.value[type] = { conversations: [], currentId: '', messages: [] };
    }
    
    const response = await sessionAPI.getSessionHistory(sessionId);
    typeSessions.value[type].messages = (response.messages || []).map((msg, idx) => ({
      id: idx + 1,
      sender: msg.role === 'user' ? 'user' : 'bot',
      timestamp: formatTime(new Date(msg.timestamp * 1000)),
      content: msg.content
    }));
  } catch (error) {
    console.error(`加载${type}历史失败:`, error);
    // 确保即使出错也有空数组
    if (!typeSessions.value[type]) {
      typeSessions.value[type] = { conversations: [], currentId: '', messages: [] };
    }
    if (typeSessions.value[type]) {
      typeSessions.value[type].messages = [];
    }
  }
};

// 创建新会话（指定type）
const handleTypeNewConversation = async (type) => {
  try {
    // 确保type存在
    if (!typeSessions.value[type]) {
      typeSessions.value[type] = { conversations: [], currentId: '', messages: [] };
    }
    
    const response = await sessionAPI.createSession(type);
    const newSession = {
      id: response.session_id,
      title: response.title || '新对话',
      time: formatDate(new Date(response.created_at)),
      updatedAt: formatTime(),
      type: response.type || type
    };
    typeSessions.value[type].conversations.unshift(newSession);
    typeSessions.value[type].currentId = response.session_id;
    typeSessions.value[type].messages = [];
  } catch (error) {
    console.error(`创建${type}会话失败:`, error);
    alert(`创建会话失败: ${error.message}`);
    // 确保即使出错也有空状态
    if (!typeSessions.value[type]) {
      typeSessions.value[type] = { conversations: [], currentId: '', messages: [] };
    }
  }
};

// 切换会话（指定type）
const handleTypeSwitchConversation = async (type, id) => {
  typeSessions.value[type].currentId = id;
  await loadTypeHistory(type, id);
};

// 删除会话（指定type）
const handleTypeDeleteConversation = async (type, id) => {
  try {
    await sessionAPI.deleteSession(id);
    const index = typeSessions.value[type].conversations.findIndex(c => c.id === id);
    if (index !== -1) {
      typeSessions.value[type].conversations.splice(index, 1);
      if (typeSessions.value[type].currentId === id) {
        if (typeSessions.value[type].conversations.length > 0) {
          typeSessions.value[type].currentId = typeSessions.value[type].conversations[0].id;
          await loadTypeHistory(type, typeSessions.value[type].conversations[0].id);
        } else {
          typeSessions.value[type].messages = [];
          typeSessions.value[type].currentId = '';
        }
      }
    }
  } catch (error) {
    console.error(`删除${type}会话失败:`, error);
    alert(`删除会话失败: ${error.message}`);
  }
};

// 发送消息（指定type）
const handleTypeSend = async (type, text, enableRag = true) => {
  if (!typeSessions.value[type].currentId) {
    await handleTypeNewConversation(type);
  }
  
  // 添加用户消息
  const userMessage = {
    id: typeSessions.value[type].messages.length + 1,
    sender: 'user',
    timestamp: formatTime(),
    content: text
  };
  typeSessions.value[type].messages.push(userMessage);
  
  // 更新对话标题
  const conversation = typeSessions.value[type].conversations.find(
    c => c.id === typeSessions.value[type].currentId
  );
  if (conversation && conversation.title === '新对话') {
    conversation.title = text.length > 20 ? text.substring(0, 20) + '...' : text;
    conversation.updatedAt = formatTime();
  }
  
  // 创建AI回复消息占位符
  const botMessageId = typeSessions.value[type].messages.length + 1;
  const botMessage = {
    id: botMessageId,
    sender: 'bot',
    timestamp: formatTime(),
    content: ''
  };
  typeSessions.value[type].messages.push(botMessage);
  
  // 发送消息到后端
  try {
    await chatAPI.sendMessage(
      type,
      text,
      typeSessions.value[type].currentId,
      (chunk) => {
        // 流式更新消息内容
        const msg = typeSessions.value[type].messages.find(m => m.id === botMessageId);
        if (msg) {
          msg.content += chunk;
        }
      },
      (error) => {
        console.error('发送消息失败:', error);
        const msg = typeSessions.value[type].messages.find(m => m.id === botMessageId);
        if (msg) {
          msg.content = '抱歉，发生了错误: ' + error.message;
        }
      },
      () => {
        // 完成
      },
      (metadata) => {
        const msg = typeSessions.value[type].messages.find(m => m.id === botMessageId);
        if (msg) {
          msg.metadata = metadata;
        }
      },
      { enableRag }
    );
  } catch (error) {
    console.error('发送消息失败:', error);
    const msg = typeSessions.value[type].messages.find(m => m.id === botMessageId);
    if (msg) {
      msg.content = '抱歉，发生了错误: ' + error.message;
    }
  }
};

// Conversation management
// Visitor
const visitorConversations = ref([]);
const currentVisitorConversationId = ref('');
const visitorMessages = ref([]);
const visitorExampleQuestions = [
  '嘉定校区图书馆在哪里？',
  '四平路校区地图',
  '校车时刻表(仅公开版)',
  '2025本科招生简章'
];

const visitorPopularQuestions = [
  '同济大学的校训是什么？',
  '同济大学创建于哪一年？',
  '同济大学是"985""211"高校吗？',
  '同济大学的土木工程在全国处于什么水平？'
];

// 加载访客会话列表
const loadVisitorSessions = async () => {
  try {
    const response = await sessionAPI.getSessionList('public');
    visitorConversations.value = (response.data || []).map(session => ({
      id: session.session_id,
      title: session.title || '新对话',
      time: formatDate(new Date(session.created_at)),
      updatedAt: formatTime()
    }));
  } catch (error) {
    console.error('加载会话列表失败:', error);
  }
};

// 加载会话历史
const loadVisitorHistory = async (sessionId) => {
  try {
    const response = await sessionAPI.getSessionHistory(sessionId);
    visitorMessages.value = (response.messages || []).map((msg, idx) => ({
      id: idx + 1,
      sender: msg.role === 'user' ? 'user' : 'bot',
      timestamp: formatTime(new Date(msg.timestamp * 1000)),
      content: msg.content
    }));
  } catch (error) {
    console.error('加载历史失败:', error);
  }
};

const handleVisitorNewConversation = async () => {
  try {
    const response = await sessionAPI.createSession('public');
    const newSession = {
      id: response.session_id,
      title: response.title || '新对话',
      time: formatDate(new Date(response.created_at)),
      updatedAt: formatTime()
    };
    visitorConversations.value.unshift(newSession);
    currentVisitorConversationId.value = response.session_id;
    visitorMessages.value = [];
  } catch (error) {
    console.error('创建会话失败:', error);
    alert('创建会话失败: ' + error.message);
  }
};

const handleVisitorSwitchConversation = async (id) => {
  currentVisitorConversationId.value = id;
  await loadVisitorHistory(id);
};

const handleVisitorDeleteConversation = async (id) => {
  try {
    await sessionAPI.deleteSession(id);
    const index = visitorConversations.value.findIndex(c => c.id === id);
    if (index !== -1) {
      visitorConversations.value.splice(index, 1);
      if (currentVisitorConversationId.value === id) {
        if (visitorConversations.value.length > 0) {
          currentVisitorConversationId.value = visitorConversations.value[0].id;
          await loadVisitorHistory(visitorConversations.value[0].id);
        } else {
          visitorMessages.value = [];
          currentVisitorConversationId.value = '';
        }
      }
    }
  } catch (error) {
    console.error('删除会话失败:', error);
    alert('删除会话失败: ' + error.message);
  }
};

const handleVisitorSend = async (text) => {
  if (!currentVisitorConversationId.value) {
    await handleVisitorNewConversation();
  }
  
  // 添加用户消息
  const userMessage = {
    id: visitorMessages.value.length + 1,
    sender: 'user',
    timestamp: formatTime(),
    content: text
  };
  visitorMessages.value.push(userMessage);
  
  // 更新对话标题
  const conversation = visitorConversations.value.find(c => c.id === currentVisitorConversationId.value);
  if (conversation && conversation.title === '新对话') {
    conversation.title = text.length > 20 ? text.substring(0, 20) + '...' : text;
    conversation.updatedAt = formatTime();
  }
  
  // 创建AI回复消息占位符
  const botMessageId = visitorMessages.value.length + 1;
  const botMessage = {
    id: botMessageId,
    sender: 'bot',
    timestamp: formatTime(),
    content: ''
  };
  visitorMessages.value.push(botMessage);
  
  // 发送消息到后端
  try {
    await chatAPI.sendMessage(
      'public',
      text,
      currentVisitorConversationId.value,
      (chunk) => {
        // 流式更新消息内容
        const msg = visitorMessages.value.find(m => m.id === botMessageId);
        if (msg) {
          msg.content += chunk;
        }
      },
      (error) => {
        console.error('发送消息失败:', error);
        const msg = visitorMessages.value.find(m => m.id === botMessageId);
        if (msg) {
          msg.content = '抱歉，发生了错误: ' + error.message;
        }
      },
      () => {
        // 完成
      }
    );
  } catch (error) {
    console.error('发送消息失败:', error);
    const msg = visitorMessages.value.find(m => m.id === botMessageId);
    if (msg) {
      msg.content = '抱歉，发生了错误: ' + error.message;
    }
  }
};

// Student
const studentConversations = ref([]);
const currentStudentConversationId = ref('');
const studentMessages = ref([]);
const studentExampleQuestions = [
  '嘉定校区图书馆几点关门？',
  '查看我今天有什么课程',
  '我的考试信息',
  '校园卡余额查询'
];

const loadStudentSessions = async () => {
  try {
    const response = await sessionAPI.getSessionList('internal');
    studentConversations.value = (response.data || []).map(session => ({
      id: session.session_id,
      title: session.title || '新对话',
      time: formatDate(new Date(session.created_at)),
      updatedAt: formatTime()
    }));
  } catch (error) {
    console.error('加载会话列表失败:', error);
  }
};

const loadStudentHistory = async (sessionId) => {
  try {
    const response = await sessionAPI.getSessionHistory(sessionId);
    studentMessages.value = (response.messages || []).map((msg, idx) => ({
      id: idx + 1,
      sender: msg.role === 'user' ? 'user' : 'bot',
      timestamp: formatTime(new Date(msg.timestamp * 1000)),
      content: msg.content
    }));
  } catch (error) {
    console.error('加载历史失败:', error);
  }
};

const handleStudentNewConversation = async () => {
  try {
    const response = await sessionAPI.createSession('internal');
    const newSession = {
      id: response.session_id,
      title: response.title || '新对话',
      time: formatDate(new Date(response.created_at)),
      updatedAt: formatTime()
    };
    studentConversations.value.unshift(newSession);
    currentStudentConversationId.value = response.session_id;
    studentMessages.value = [];
  } catch (error) {
    console.error('创建会话失败:', error);
    alert('创建会话失败: ' + error.message);
  }
};

const handleStudentSwitchConversation = async (id) => {
  currentStudentConversationId.value = id;
  await loadStudentHistory(id);
};

const handleStudentDeleteConversation = async (id) => {
  try {
    await sessionAPI.deleteSession(id);
    const index = studentConversations.value.findIndex(c => c.id === id);
    if (index !== -1) {
      studentConversations.value.splice(index, 1);
      if (currentStudentConversationId.value === id) {
        if (studentConversations.value.length > 0) {
          currentStudentConversationId.value = studentConversations.value[0].id;
          await loadStudentHistory(studentConversations.value[0].id);
        } else {
          studentMessages.value = [];
          currentStudentConversationId.value = '';
        }
      }
    }
  } catch (error) {
    console.error('删除会话失败:', error);
    alert('删除会话失败: ' + error.message);
  }
};

const handleStudentSend = async (text) => {
  if (!currentStudentConversationId.value) {
    await handleStudentNewConversation();
  }
  
  const userMessage = {
    id: studentMessages.value.length + 1,
    sender: 'user',
    timestamp: formatTime(),
    content: text
  };
  studentMessages.value.push(userMessage);
  
  const conversation = studentConversations.value.find(c => c.id === currentStudentConversationId.value);
  if (conversation && conversation.title === '新对话') {
    conversation.title = text.length > 20 ? text.substring(0, 20) + '...' : text;
    conversation.updatedAt = formatTime();
  }
  
  const botMessageId = studentMessages.value.length + 1;
  const botMessage = {
    id: botMessageId,
    sender: 'bot',
    timestamp: formatTime(),
    content: ''
  };
  studentMessages.value.push(botMessage);
  
  try {
    await chatAPI.sendMessage(
      'internal',
      text,
      currentStudentConversationId.value,
      (chunk) => {
        const msg = studentMessages.value.find(m => m.id === botMessageId);
        if (msg) {
          msg.content += chunk;
        }
      },
      (error) => {
        console.error('发送消息失败:', error);
        const msg = studentMessages.value.find(m => m.id === botMessageId);
        if (msg) {
          msg.content = '抱歉，发生了错误: ' + error.message;
        }
      },
      () => {}
    );
  } catch (error) {
    console.error('发送消息失败:', error);
    const msg = studentMessages.value.find(m => m.id === botMessageId);
    if (msg) {
      msg.content = '抱歉，发生了错误: ' + error.message;
    }
  }
};
// 根据当前模式和用户信息返回用户对象（计算属性）
const currentUser = computed(() => {
  if (currentMode.value === 'visitor') {
    return { name: 'Anonymous Guest', id: 'Guest', role: 'Visitor', avatar: 'V', department: '访客' };
  } else if (currentMode.value === 'denied') {
    return { name: 'Anonymous Guest', id: 'Guest', role: 'Visitor', avatar: 'V', department: '访客' };
  } else if (currentUserInfo.value) {
    // 使用从API返回的真实用户信息
    const userInfo = currentUserInfo.value;
    const name = userInfo.name || '用户';
    const role = userInfo.role || 'student';
    const roleLower = role.toLowerCase();
    
    // 根据角色生成头像和部门信息
    let avatar = name.substring(0, 2).toUpperCase();
    let department = '';
    let displayRole = '';
    let userId = '';
    
    if (roleLower.includes('student')) {
      displayRole = '在校师生 (Student)';
      department = userInfo.department || '学生';
      avatar = name.substring(0, 2).toUpperCase();
      userId = userInfo.id || userInfo.username || 'Student';
    } else if (roleLower.includes('teacher')) {
      displayRole = '教师 (Teacher)';
      department = userInfo.department || '教师';
      avatar = name.substring(0, 2).toUpperCase();
      userId = userInfo.id || userInfo.username || 'Faculty';
    } else if (roleLower.includes('admin')) {
      displayRole = '管理员 (Admin)';
      department = userInfo.department || '系统管理';
      avatar = 'AD';
      userId = userInfo.id || userInfo.username || 'Admin';
    } else {
      displayRole = role;
      department = userInfo.department || '';
      avatar = name.substring(0, 2).toUpperCase();
      userId = userInfo.id || userInfo.username || 'User';
    }
    
    return {
      name: name,
      id: userId,
      role: displayRole,
      avatar: avatar,
      department: department
    };
  }
  return { name: '用户', id: 'User', role: '在校师生 (Student)', avatar: 'U', department: '学生' };
});

const studentWelcomeTitle = computed(() => {
  return `欢迎回来，${currentUser.value.name}！`;
});

// 根据用户身份返回对应的ID Card英文标题
const userCardTitle = computed(() => {
  const role = currentUser.value.role || '';
  const roleLower = role.toLowerCase();
  
  if (roleLower.includes('visitor') || roleLower.includes('guest')) {
    return 'Visitor Card';
  } else if (roleLower.includes('admin')) {
    return 'Admin Card';
  } else if (roleLower.includes('teacher') || roleLower.includes('faculty')) {
    return 'Faculty ID Card';
  } else if (roleLower.includes('student')) {
    return 'Student ID Card';
  } else {
    // 默认根据模式判断
    if (currentMode.value === 'student') {
      return 'Student ID Card';
    } else {
      return 'Visitor Card';
    }
  }
});

// 根据用户身份返回对应的英文身份标识
const userRoleEnglish = computed(() => {
  const role = currentUser.value.role || '';
  const roleLower = role.toLowerCase();
  
  if (roleLower.includes('visitor') || roleLower.includes('guest')) {
    return 'Visitor';
  } else if (roleLower.includes('admin')) {
    return 'Admin';
  } else if (roleLower.includes('teacher') || roleLower.includes('faculty')) {
    return 'Faculty';
  } else if (roleLower.includes('student')) {
    return 'Student';
  } else {
    // 默认根据模式判断
    if (currentMode.value === 'student') {
      return 'Student';
    } else {
      return 'Visitor';
    }
  }
});

// Access denied
const deniedConversations = ref([]);
const currentDeniedConversationId = ref('');
const deniedMessages = ref([]);
const deniedExampleQuestions = [
  '帮我查一下我的高数期末成绩',
  '查看我的个人课表',
  '我的选课信息',
  '四平路校区地图'
];

const handleDeniedNewConversation = () => {
  // Denied模式使用本地生成的ID，不需要API调用
  const newId = `denied_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  deniedConversations.value.unshift({
    id: newId,
    title: '新对话',
    time: formatDate(),
    updatedAt: formatTime()
  });
  currentDeniedConversationId.value = newId;
  deniedMessages.value = [];
};

const handleDeniedSwitchConversation = (id) => {
  currentDeniedConversationId.value = id;
  deniedMessages.value = [];
};

const handleDeniedDeleteConversation = (id) => {
  const index = deniedConversations.value.findIndex(c => c.id === id);
  if (index !== -1) {
    deniedConversations.value.splice(index, 1);
    if (currentDeniedConversationId.value === id) {
      if (deniedConversations.value.length > 0) {
        currentDeniedConversationId.value = deniedConversations.value[0].id;
        deniedMessages.value = [];
      } else {
        handleDeniedNewConversation();
      }
    }
  }
};

const handleDeniedSend = async (text) => {
  if (!currentDeniedConversationId.value) {
    await handleDeniedNewConversation();
  }
  
  const userMessage = {
    id: deniedMessages.value.length + 1,
    sender: 'user',
    timestamp: formatTime(),
    content: text
  };
  deniedMessages.value.push(userMessage);
  
  const conversation = deniedConversations.value.find(c => c.id === currentDeniedConversationId.value);
  if (conversation && conversation.title === '新对话') {
    conversation.title = text.length > 20 ? text.substring(0, 20) + '...' : text;
    conversation.updatedAt = formatTime();
  }
  
  // 权限不足提示
  setTimeout(() => {
    deniedMessages.value.push({
      id: deniedMessages.value.length + 1,
      sender: 'bot',
      timestamp: formatTime(),
      content: '权限不足。请点击左下角登录。'
    });
  }, 800);
};

const handleLogin = async () => {
  if (loggingIn.value) return;
  if (!loginForm.value.email || !loginForm.value.password) {
    loginError.value = '请输入用户名和密码';
    return;
  }
  
  loggingIn.value = true;
  loginError.value = '';
  
  try {
    // 使用email字段作为username
    const response = await authAPI.login(loginForm.value.email, loginForm.value.password);
    
    // 保存用户信息
    currentUserInfo.value = response.user_info || {};
    
    // 处理"记住登录"功能
    if (rememberLogin.value) {
      // 保存用户名到localStorage
      localStorage.setItem('remembered_username', loginForm.value.email);
    } else {
      // 清除保存的用户名
      localStorage.removeItem('remembered_username');
    }
    
    // 根据返回的角色决定模式
    const role = currentUserInfo.value.role || 'student';
    currentMode.value = getModeByRole(role);
    
    // 登录成功
    loggingIn.value = false;
    showLogin.value = false;
    
    currentType.value = 'auto';
    await loadTypeSessions('auto');
    
    // 为当前type初始化会话
    if (typeSessions.value[currentType.value].conversations.length === 0) {
      await handleTypeNewConversation(currentType.value);
    } else {
      typeSessions.value[currentType.value].currentId = 
        typeSessions.value[currentType.value].conversations[0].id;
      await loadTypeHistory(
        currentType.value,
        typeSessions.value[currentType.value].conversations[0].id
      );
    }
  } catch (error) {
    loggingIn.value = false;
    loginError.value = error.message || '登录失败，请检查用户名和密码';
    console.error('登录失败:', error);
  }
};

const handleGuestLogin = async () => {
  try {
    const response = await authAPI.guestLogin();
    // 保存访客用户信息
    currentUserInfo.value = response.user_info || {};
    showLogin.value = false;
    currentMode.value = 'visitor';
    currentType.value = 'auto';
    await loadTypeSessions('auto');
    if (typeSessions.value.auto.conversations.length === 0) {
      await handleTypeNewConversation('auto');
    } else {
      typeSessions.value.auto.currentId = typeSessions.value.auto.conversations[0].id;
      await loadTypeHistory('auto', typeSessions.value.auto.conversations[0].id);
    }
  } catch (error) {
    console.error('访客登录失败:', error);
    alert('访客登录失败: ' + error.message);
  }
};

const handleLogout = async () => {
  try {
    await authAPI.logout();
  } catch (error) {
    console.error('登出失败:', error);
  }
  
  // 重置所有状态
  showLogin.value = true;
  showAdminPanel.value = false;
  currentMode.value = 'visitor';
  currentType.value = 'auto';
  
  // 清空所有type的会话数据
  typeSessions.value = {
    auto: { conversations: [], currentId: '', messages: [] },
    public: { conversations: [], currentId: '', messages: [] },
    academic: { conversations: [], currentId: '', messages: [] },
    internal: { conversations: [], currentId: '', messages: [] },
    personal: { conversations: [], currentId: '', messages: [] }
  };
  
  // 保留向后兼容（清空旧的独立状态）
  visitorConversations.value = [];
  studentConversations.value = [];
  deniedConversations.value = [];
  
  currentVisitorConversationId.value = '';
  currentStudentConversationId.value = '';
  currentDeniedConversationId.value = '';
  
  visitorMessages.value = [];
  studentMessages.value = [];
  deniedMessages.value = [];
  
  // 重置登录表单和用户信息
  loginError.value = '';
  currentUserInfo.value = null;
  
  // 恢复记住的用户名，密码始终由用户重新输入
  const rememberedUsername = localStorage.getItem('remembered_username');
  localStorage.removeItem('remembered_password');
  
  if (rememberedUsername) {
    loginForm.value.email = rememberedUsername;
    rememberLogin.value = true;
  } else {
    loginForm.value.email = '';
    rememberLogin.value = false;
  }
  
  loginForm.value.password = '';
};

// 检查是否已登录
onMounted(async () => {
  // 恢复"记住登录"的用户名
  const rememberedUsername = localStorage.getItem('remembered_username');
  if (rememberedUsername) {
    loginForm.value.email = rememberedUsername;
    rememberLogin.value = true;
  }
  
  localStorage.removeItem('remembered_password');
  
  if (isAuthenticated()) {
    const userInfo = getUserInfo();
    if (userInfo) {
      // 恢复用户信息
      currentUserInfo.value = userInfo;
      // 根据角色决定模式
      const role = userInfo.role || 'student';
      currentMode.value = getModeByRole(role);
      showLogin.value = false;
      
      currentType.value = 'auto';
      await loadTypeSessions('auto');
      
      // 为当前type初始化会话
      if (typeSessions.value[currentType.value].conversations.length > 0) {
        typeSessions.value[currentType.value].currentId = 
          typeSessions.value[currentType.value].conversations[0].id;
        await loadTypeHistory(
          currentType.value,
          typeSessions.value[currentType.value].conversations[0].id
        );
      } else {
        await handleTypeNewConversation(currentType.value);
      }
    } else {
      showLogin.value = true;
    }
  }
});
</script>

<template>
  <div v-if="showLogin" class="min-h-screen bg-[radial-gradient(circle_at_15%_20%,_#164e63_0,_#172033_38%,_#0f172a_100%)] flex items-center justify-center px-4 py-10 relative overflow-hidden">
    <div class="absolute -top-32 -right-24 w-96 h-96 rounded-full bg-emerald-400/10 blur-3xl"></div>
    <div class="absolute -bottom-40 -left-24 w-[30rem] h-[30rem] rounded-full bg-cyan-400/10 blur-3xl"></div>
    <div class="max-w-5xl w-full grid md:grid-cols-2 gap-8 items-center">
      <div class="text-white space-y-6">
        <div class="flex items-center gap-3 text-2xl font-bold">
          <div class="w-11 h-11 rounded-2xl bg-gradient-to-br from-cyan-300 to-emerald-300 text-slate-950 flex items-center justify-center shadow-xl shadow-cyan-950/30">S</div>
          <div>
            <div>SynapseQ</div>
            <div class="text-[10px] font-normal tracking-[0.25em] text-cyan-100/70">同济校园智能问答</div>
          </div>
        </div>
        <div>
          <div class="text-4xl md:text-5xl font-semibold leading-tight tracking-tight">让校园信息更近一步</div>
          <p class="mt-4 text-slate-300 max-w-lg leading-7">系统会按登录身份自动检索公开资料、校内信息与个人档案，无需手动选择模块。</p>
        </div>
        <div class="flex flex-wrap gap-3 text-xs text-slate-200">
          <span class="px-3 py-1 rounded-full bg-white/10 border border-white/10 flex items-center gap-1">
            <Shield class="w-4 h-4" /> 校内访问隔离
          </span>
          <span class="px-3 py-1 rounded-full bg-white/10 border border-white/10 flex items-center gap-1">
            <Database class="w-4 h-4" /> 内部/个人双向量库
          </span>
          <span class="px-3 py-1 rounded-full bg-white/10 border border-white/10 flex items-center gap-1">
            <Sparkles class="w-4 h-4" /> 上下文智能理解
          </span>
        </div>
        <div class="hidden md:block bg-white/[0.06] border border-white/10 rounded-3xl p-5 shadow-2xl backdrop-blur">
          <div class="text-sm font-semibold flex items-center gap-2 text-white mb-2">
            <Database class="w-4 h-4" /> 预览已连接集合
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs text-slate-200">
            <div class="p-3 rounded-lg bg-white/5 border border-white/10">
              <div class="flex items-center gap-2">
                <div class="w-2 h-2 rounded-full bg-green-400"></div>
                Standard (公开)
              </div>
              <div class="mt-1 text-[11px] text-slate-300/80">校区指南/公开资讯</div>
            </div>
            <div class="p-3 rounded-lg bg-white/5 border border-white/10">
              <div class="flex items-center gap-2">
                <div class="w-2 h-2 rounded-full bg-blue-400"></div>
                Internal (内部)
              </div>
              <div class="mt-1 text-[11px] text-slate-300/80">通知/服务/课表</div>
            </div>
            <div class="p-3 rounded-lg bg-white/5 border border-white/10">
              <div class="flex items-center gap-2">
                <div class="w-2 h-2 rounded-full bg-orange-400"></div>
                Person_info (个人)
              </div>
              <div class="mt-1 text-[11px] text-slate-300/80">成绩/选课/私有内容</div>
            </div>
          </div>
        </div>
      </div>

      <div class="bg-white/95 backdrop-blur rounded-3xl shadow-2xl border border-white/70 p-8 space-y-6 relative z-10">
          <div class="space-y-1">
          <div class="text-xl font-semibold text-slate-900">
            统一身份登录
          </div>
          <p class="text-sm text-slate-500">
            使用统一身份认证账号登录
          </p>
        </div>

        <div class="space-y-4">
          <div v-if="loginError" class="p-3 bg-red-50 border border-red-200 rounded-xl text-sm text-red-700">
            {{ loginError }}
          </div>
          <div>
            <label class="text-xs font-semibold text-slate-600 block mb-2">用户名</label>
            <div class="relative">
              <User class="w-4 h-4 text-slate-400 absolute left-3 top-3" />
              <input
                v-model="loginForm.email"
                type="text"
                placeholder="请输入用户名"
                class="w-full pl-9 pr-3 py-2.5 rounded-xl bg-slate-50 border border-slate-200 focus:outline-none focus:ring-2 focus:ring-cyan-200 focus:border-cyan-500 text-sm"
              />
            </div>
          </div>
          <div>
            <label class="text-xs font-semibold text-slate-600 block mb-2">密码</label>
            <div class="relative">
              <Shield class="w-4 h-4 text-slate-400 absolute left-3 top-3" />
              <input
                v-model="loginForm.password"
                type="password"
                placeholder="••••••••"
                class="w-full pl-9 pr-3 py-2.5 rounded-xl bg-slate-50 border border-slate-200 focus:outline-none focus:ring-2 focus:ring-cyan-200 focus:border-cyan-500 text-sm"
              />
            </div>
            <div class="flex items-center justify-end text-xs text-slate-500 mt-2">
              <label class="inline-flex items-center gap-2 cursor-pointer">
                <input 
                  type="checkbox" 
                  v-model="rememberLogin"
                  class="rounded border-slate-300 text-cyan-700 focus:ring-cyan-200"
                />
                记住用户名
              </label>
            </div>
          </div>
        </div>

        <div class="space-y-3">
          <button
            class="w-full py-2.5 rounded-xl bg-cyan-700 text-white font-semibold shadow-lg shadow-cyan-900/20 hover:bg-cyan-800 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
            :disabled="loggingIn || !loginForm.email || !loginForm.password"
            @click="handleLogin"
          >
            <span v-if="!loggingIn">
              登录
            </span>
            <span v-else>正在登录...</span>
          </button>
          
          <button
            @click="handleGuestLogin"
            class="w-full py-2.5 rounded-xl bg-slate-100 text-slate-700 font-medium hover:bg-slate-200 transition-colors flex items-center justify-center gap-2"
          >
            <Globe class="w-4 h-4" />
            以访客身份进入（无需登录）
          </button>
        </div>

        <div class="text-[11px] text-slate-400 leading-relaxed">
          登录即同意 <a class="text-cyan-700 hover:underline" href="#">用户协议</a> 与 <a class="text-cyan-700 hover:underline" href="#">隐私政策</a>。
        </div>
      </div>
    </div>
  </div>
  <div v-else class="h-screen flex flex-col bg-white">
    <div class="flex-1 overflow-hidden relative">
      <div class="w-full h-full bg-white overflow-hidden">
        <!-- 统一的Type管理界面 -->
        <SynapseQShell
          :key="currentType"
          :themeColor="currentTypeConfig.color"
          :headerTitle="currentTypeConfig.title"
          :headerBadge="currentTypeConfig.badge"
          :currentUser="currentUser"
          :conversationHistory="currentConversations"
          :currentConversationId="currentConversationId"
          :messages="currentMessages"
          :exampleQuestions="getExampleQuestions(currentType)"
          :welcomeTitle="getWelcomeTitle(currentType)"
          @send-message="(text, enableRag) => handleTypeSend(currentType, text, enableRag)"
          @new-conversation="() => handleTypeNewConversation(currentType)"
          @switch-conversation="(id) => handleTypeSwitchConversation(currentType, id)"
          @delete-conversation="(id) => handleTypeDeleteConversation(currentType, id)"
          @logout="handleLogout"
        >
          <template #right-panel>
            <div :class="getUserPanelGradientClass()">
              <div class="absolute top-0 right-0 p-3 opacity-20">
                <Shield size="64" />
              </div>
              <div class="relative z-10">
                <div class="text-xs opacity-80 uppercase tracking-widest mb-1">{{ userCardTitle }}</div>
                <div class="text-2xl font-bold tracking-tight mb-4">{{ currentUser.id }}</div>
                <div class="flex items-end justify-between">
                  <div>
                    <div class="text-sm font-medium">{{ currentUser.name }}</div>
                    <div class="text-xs opacity-80">{{ currentUser.department }}</div>
                  </div>
                  <div class="w-10 h-10 rounded bg-white/20 backdrop-blur-sm flex items-center justify-center font-bold text-lg">
                    {{ currentUser.avatar }}
                  </div>
                </div>
              </div>
            </div>

            <div class="mt-4">
              <h4 class="text-xs font-bold text-gray-400 uppercase tracking-wider mb-3">已连接的向量集合</h4>
              <div class="space-y-2">
                <div v-for="collection in getUserConnectedCollections()" :key="collection.name"
                  :class="getCollectionItemClass(collection.color)">
                  <div class="flex items-center gap-2">
                    <div :class="getCollectionDotClass(collection.color)"></div>
                    <span class="text-sm font-medium text-gray-700">{{ collection.name }}</span>
                  </div>
                  <span class="text-xs bg-white px-2 py-0.5 rounded text-gray-500 border border-gray-100">{{ collection.access }}</span>
                </div>
              </div>
            </div>

            <div class="mt-4" v-if="isAdminUser">
              <button
                @click="showAdminPanel = true"
                class="w-full p-3 bg-cyan-700 text-white rounded-lg text-sm font-semibold cursor-pointer transition-colors flex items-center justify-center gap-2 hover:bg-cyan-800 shadow-sm"
              >
                管理后台
              </button>
            </div>

            <div class="mt-4" v-if="getUserCommonServices().length > 0">
              <h4 class="text-xs font-bold text-gray-400 uppercase tracking-wider mb-3 flex items-center gap-2">
                <FileText v-if="currentMode === 'student'" size="14" />
                <Search v-else size="14" />
                {{ currentMode === 'student' ? '常用服务' : '快速访问' }}
              </h4>
              <div class="space-y-2">
                <a
                  v-for="service in getUserCommonServices()"
                  :key="service.name"
                  :href="service.url"
                  target="_blank"
                  rel="noopener noreferrer"
                  :class="getServiceLinkClass()"
                >
                  <span>{{ service.name }}</span>
                  <ChevronRight size="12" class="opacity-100 transition-opacity" />
                </a>
              </div>
            </div>

            <div class="mt-4" v-if="currentMode === 'visitor' && getPopularQuestions('public').length > 0">
              <h4 class="text-xs font-bold text-gray-400 uppercase tracking-wider mb-3">热门问题</h4>
              <div class="space-y-2">
                <button
                  v-for="q in getPopularQuestions('public')"
                  :key="q"
                  @click="handleTypeSend('auto', q)"
                  class="w-full p-3 bg-white border border-gray-100 rounded-lg text-xs text-gray-600 hover:border-green-200 hover:text-green-700 cursor-pointer transition-colors flex items-center justify-between group text-left"
                >
                  <span>{{ q }}</span>
                  <ChevronRight size="12" class="opacity-0 group-hover:opacity-100 transition-opacity" />
                </button>
              </div>
            </div>
          </template>
        </SynapseQShell>

        <AdminPanel
          v-if="showAdminPanel && isAdminUser"
          @close="showAdminPanel = false"
        />
        
        <!-- 保留向后兼容的旧模式组件 -->
        <!-- Visitor -->
        <SynapseQShell
          v-if="false && currentMode === 'visitor'"
          themeColor="green"
          headerTitle="访客通道 (Public Access)"
          headerBadge="Standard 库已连接"
          :currentUser="currentUser"
          :conversationHistory="visitorConversations"
          :currentConversationId="currentVisitorConversationId"
          :messages="visitorMessages"
          :exampleQuestions="visitorExampleQuestions"
          welcomeTitle="欢迎使用 SynapseQ 访客模式"
          @send-message="handleVisitorSend"
          @new-conversation="handleVisitorNewConversation"
          @switch-conversation="handleVisitorSwitchConversation"
          @delete-conversation="handleVisitorDeleteConversation"
          @logout="handleLogout"
        >
          <template #right-panel>
            <div class="bg-gradient-to-br from-green-500 to-emerald-600 rounded-xl p-4 text-white shadow-lg relative overflow-hidden">
              <div class="absolute top-0 right-0 p-3 opacity-20">
                <Shield size="64" />
              </div>
              <div class="relative z-10">
                <div class="text-xs opacity-80 uppercase tracking-widest mb-1">{{ userCardTitle }}</div>
                <div class="text-2xl font-bold tracking-tight mb-4">{{ currentUser.id }}</div>
                <div class="flex items-end justify-between">
                  <div>
                    <div class="text-sm font-medium">{{ currentUser.name }}</div>
                    <div class="text-xs opacity-80">{{ currentUser.department }}</div>
                  </div>
                  <div class="w-10 h-10 rounded bg-white/20 backdrop-blur-sm flex items-center justify-center font-bold text-lg">
                    {{ currentUser.avatar }}
                  </div>
                </div>
              </div>
            </div>

            <div class="mt-4">
              <h4 class="text-xs font-bold text-gray-400 uppercase tracking-wider mb-3">已连接的向量集合</h4>
              <div class="space-y-2">
                <div class="flex items-center justify-between p-3 bg-green-50 rounded-lg border border-green-100">
                  <div class="flex items-center gap-2">
                    <div class="w-2 h-2 rounded-full bg-green-500"></div>
                    <span class="text-sm font-medium text-gray-700">Standard (公开)</span>
                  </div>
                  <span class="text-xs bg-white px-2 py-0.5 rounded text-gray-500 border border-gray-100">Read</span>
                </div>
              </div>
            </div>

            <div class="mt-4">
              <h4 class="text-xs font-bold text-gray-400 uppercase tracking-wider mb-3">热门公开问题</h4>
              <div class="space-y-2">
                <button
                  v-for="q in visitorPopularQuestions"
                  :key="q"
                  @click="handleVisitorSend(q)"
                  class="w-full p-3 bg-white border border-gray-100 rounded-lg text-xs text-gray-600 hover:border-green-200 hover:text-green-700 cursor-pointer transition-colors flex items-center justify-between group text-left"
                >
                  <span>{{ q }}</span>
                  <ChevronRight size="12" class="opacity-0 group-hover:opacity-100 transition-opacity" />
                </button>
              </div>
            </div>
          </template>
        </SynapseQShell>

        <!-- Student -->
        <SynapseQShell
          v-if="false && currentMode === 'student'"
          themeColor="blue"
          headerTitle="校内服务通道 (Campus Services)"
          headerBadge="Internal 库已连接"
          :currentUser="currentUser"
          :conversationHistory="studentConversations"
          :currentConversationId="currentStudentConversationId"
          :messages="studentMessages"
          :exampleQuestions="studentExampleQuestions"
          :welcomeTitle="studentWelcomeTitle"
          @send-message="handleStudentSend"
          @new-conversation="handleStudentNewConversation"
          @switch-conversation="handleStudentSwitchConversation"
          @delete-conversation="handleStudentDeleteConversation"
          @logout="handleLogout"
        >
          <template #right-panel>
            <div class="bg-gradient-to-br from-blue-500 to-indigo-600 rounded-xl p-4 text-white shadow-lg relative overflow-hidden">
              <div class="absolute top-0 right-0 p-3 opacity-20">
            <Shield size="64" />
          </div>
          <div class="relative z-10">
            <div class="text-xs opacity-80 uppercase tracking-widest mb-1">{{ userCardTitle }}</div>
                <div class="text-2xl font-bold tracking-tight mb-4">{{ currentUser.id }}</div>
            <div class="flex items-end justify-between">
              <div>
                    <div class="text-sm font-medium">{{ currentUser.name }}</div>
                <div class="text-xs opacity-80">{{ currentUser.department }}</div>
              </div>
              <div class="w-10 h-10 rounded bg-white/20 backdrop-blur-sm flex items-center justify-center font-bold text-lg">
                    {{ currentUser.avatar }}
              </div>
            </div>
          </div>
        </div>

            <div class="mt-4">
          <h4 class="text-xs font-bold text-gray-400 uppercase tracking-wider mb-3">已连接的向量集合</h4>
          <div class="space-y-2">
            <div class="flex items-center justify-between p-3 bg-green-50 rounded-lg border border-green-100">
              <div class="flex items-center gap-2">
                <div class="w-2 h-2 rounded-full bg-green-500"></div>
                <span class="text-sm font-medium text-gray-700">Standard (公开)</span>
              </div>
              <span class="text-xs bg-white px-2 py-0.5 rounded text-gray-500 border border-gray-100">Read</span>
            </div>
            <div class="flex items-center justify-between p-3 bg-blue-50 rounded-lg border border-blue-100">
              <div class="flex items-center gap-2">
                <div class="w-2 h-2 rounded-full bg-blue-500"></div>
                <span class="text-sm font-medium text-gray-700">Internal (内部)</span>
              </div>
              <span class="text-xs bg-white px-2 py-0.5 rounded text-gray-500 border border-gray-100">Read</span>
            </div>
            <div class="flex items-center justify-between p-3 bg-orange-50 rounded-lg border border-orange-100">
              <div class="flex items-center gap-2">
                <div class="w-2 h-2 rounded-full bg-orange-500"></div>
                <span class="text-sm font-medium text-gray-700">Person_info (个人)</span>
              </div>
              <span class="text-xs bg-white px-2 py-0.5 rounded text-gray-500 border border-gray-100">Private</span>
            </div>
          </div>
        </div>

            <div class="mt-4">
          <h4 class="text-xs font-bold text-gray-400 uppercase tracking-wider mb-3 flex items-center gap-2">
            <FileText size="14" /> 常用服务
          </h4>
          <div class="space-y-2">
            <a
              v-for="service in [
                { name: '教学信息管理系统', url: 'https://1.tongji.edu.cn/' },
                { name: 'canvas', url: 'https://canvas.tongji.edu.cn/' },
                { name: '同济邮箱', url: 'https://mail.tongji.edu.cn/' }
              ]"
              :key="service.name"
              :href="service.url"
              target="_blank"
              rel="noopener noreferrer"
              class="w-full p-3 bg-white border border-blue-200 text-blue-700 rounded-lg text-xs cursor-pointer transition-colors flex items-center justify-between group text-left hover:bg-blue-50 hover:border-blue-300"
            >
              <span>{{ service.name }}</span>
              <ChevronRight size="12" class="opacity-100 transition-opacity" />
            </a>
          </div>
        </div>
          </template>
        </SynapseQShell>

        <!-- Access Denied -->
        <SynapseQShell
          v-if="false && currentMode === 'denied'"
          themeColor="green"
          headerTitle="访客通道 (Public Access)"
          headerBadge="Standard 库已连接"
          :currentUser="currentUser"
          :conversationHistory="deniedConversations"
          :currentConversationId="currentDeniedConversationId"
          :messages="deniedMessages"
          :exampleQuestions="deniedExampleQuestions"
          welcomeTitle="欢迎使用 SynapseQ 访客模式"
          @send-message="handleDeniedSend"
          @new-conversation="handleDeniedNewConversation"
          @switch-conversation="handleDeniedSwitchConversation"
          @delete-conversation="handleDeniedDeleteConversation"
          @logout="handleLogout"
        >
          <template #right-panel>
            <div class="bg-gradient-to-br from-green-500 to-emerald-600 rounded-xl p-4 text-white shadow-lg relative overflow-hidden">
              <div class="absolute top-0 right-0 p-3 opacity-20">
                <Shield size="64" />
              </div>
              <div class="relative z-10">
                <div class="text-xs opacity-80 uppercase tracking-widest mb-1">{{ userCardTitle }}</div>
                <div class="text-2xl font-bold tracking-tight mb-4">{{ currentUser.id }}</div>
                <div class="flex items-end justify-between">
                  <div>
                    <div class="text-sm font-medium">{{ currentUser.name }}</div>
                    <div class="text-xs opacity-80">{{ currentUser.department }}</div>
                  </div>
                  <div class="w-10 h-10 rounded bg-white/20 backdrop-blur-sm flex items-center justify-center font-bold text-lg">
                    {{ currentUser.avatar }}
                  </div>
                </div>
              </div>
            </div>

            <div class="mt-4">
              <h4 class="text-xs font-bold text-gray-400 uppercase tracking-wider mb-3">已连接的向量集合</h4>
              <div class="space-y-2">
                <div class="flex items-center justify-between p-3 bg-green-50 rounded-lg border border-green-100">
                  <div class="flex items-center gap-2">
                    <div class="w-2 h-2 rounded-full bg-green-500"></div>
                    <span class="text-sm font-medium text-gray-700">Standard (公开)</span>
                  </div>
                  <span class="text-xs bg-white px-2 py-0.5 rounded text-gray-500 border border-gray-100">Read</span>
                </div>
              </div>
            </div>

            <div class="mt-4 p-3 bg-red-50 border border-red-100 rounded-lg flex items-start gap-2">
              <AlertCircle size="14" class="text-red-500 mt-0.5 flex-shrink-0" />
              <div class="text-xs text-red-700">
                <span class="font-bold">提示：</span> 刚才的请求因涉及隐私被拦截。请登录以访问个人数据。
              </div>
            </div>
          </template>
        </SynapseQShell>
      </div>
    </div>
  </div>
</template>

