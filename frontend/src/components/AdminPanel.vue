<script setup>
import { computed, onMounted, ref } from 'vue';
import { BookOpen, Database, FilePlus, Pencil, RefreshCw, Save, Trash2, X } from 'lucide-vue-next';
import { adminAPI } from '../api/api.js';

const emit = defineEmits(['close']);

const activeTab = ref('crawl');
const loading = ref(false);
const message = ref('');

const faqs = ref([]);
const selectedFaq = ref(null);
const faqForm = ref({ question: '', answer: '', source: '', aliases: '', is_active: true });

const knowledge = ref([]);
const collectionFilter = ref('');
const selectedKnowledge = ref(null);
const knowledgeForm = ref({
  title: '',
  section: '',
  url: '',
  access_scope: 'public',
  text_content: ''
});
const confirmDialog = ref({
  visible: false,
  title: '',
  body: '',
  danger: false,
  action: null
});

const crawlUrl = ref('https://www.tongji.edu.cn/');
const maxPages = ref(8);
const accessScope = ref('public');
const previewBlocks = ref([]);
const sourceUrl = ref('');

const groupedBlocks = computed(() => {
  const groups = {};
  for (const block of previewBlocks.value) {
    const key = block.section || '综合资料';
    groups[key] ||= [];
    groups[key].push(block);
  }
  return groups;
});

const setMessage = (text) => {
  message.value = text;
  if (text) {
    window.setTimeout(() => {
      if (message.value === text) message.value = '';
    }, 3000);
  }
};

const askConfirm = ({ title, body, danger = false, action }) => {
  confirmDialog.value = {
    visible: true,
    title,
    body,
    danger,
    action
  };
};

const closeConfirm = () => {
  confirmDialog.value = {
    visible: false,
    title: '',
    body: '',
    danger: false,
    action: null
  };
};

const confirmAction = async () => {
  const action = confirmDialog.value.action;
  closeConfirm();
  if (action) await action();
};

const loadFaqs = async () => {
  faqs.value = await adminAPI.listFaqs();
};

const loadKnowledge = async () => {
  knowledge.value = await adminAPI.listKnowledge(collectionFilter.value);
  if (selectedKnowledge.value) {
    const refreshed = knowledge.value.find((item) => item.id === selectedKnowledge.value.id);
    if (!refreshed) clearKnowledgeSelection();
  }
};

const selectFaq = (faq) => {
  selectedFaq.value = faq;
  faqForm.value = {
    question: faq.question,
    answer: faq.answer,
    source: faq.source || '',
    aliases: (faq.aliases || []).join('\n'),
    is_active: faq.is_active
  };
};

const newFaq = () => {
  selectedFaq.value = null;
  faqForm.value = { question: '', answer: '', source: '后台FAQ', aliases: '', is_active: true };
};

const saveFaq = async () => {
  loading.value = true;
  try {
    const payload = {
      question: faqForm.value.question,
      answer: faqForm.value.answer,
      source: faqForm.value.source,
      aliases: faqForm.value.aliases.split('\n').map((item) => item.trim()).filter(Boolean),
      is_active: faqForm.value.is_active
    };
    if (selectedFaq.value) {
      await adminAPI.updateFaq(selectedFaq.value.id, payload);
    } else {
      await adminAPI.createFaq(payload);
    }
    await loadFaqs();
    newFaq();
    setMessage('FAQ 已保存并同步到向量库');
  } catch (error) {
    setMessage(error.message || 'FAQ 保存失败');
  } finally {
    loading.value = false;
  }
};

const deleteFaq = async () => {
  if (!selectedFaq.value) return;
  askConfirm({
    title: '系统提示',
    body: '确定删除这条 FAQ 吗？确认后会删除数据库记录，并同步刷新 FAQ 向量库。',
    danger: true,
    action: async () => {
      loading.value = true;
      try {
        await adminAPI.deleteFaq(selectedFaq.value.id);
        await Promise.all([loadFaqs(), loadKnowledge()]);
        newFaq();
        setMessage('FAQ 已删除并同步到向量库');
      } catch (error) {
        setMessage(error.message || 'FAQ 删除失败');
      } finally {
        loading.value = false;
      }
    }
  });
};

const selectKnowledge = (item) => {
  selectedKnowledge.value = item;
  knowledgeForm.value = {
    title: item.title || '',
    section: item.section || '',
    url: item.url || '',
    access_scope: item.access_scope || (item.collection_name === 'rag_standard' ? 'public' : 'campus'),
    text_content: item.text_content || item.text_preview || ''
  };
};

const clearKnowledgeSelection = () => {
  selectedKnowledge.value = null;
  knowledgeForm.value = {
    title: '',
    section: '',
    url: '',
    access_scope: 'public',
    text_content: ''
  };
};

const saveKnowledge = async () => {
  if (!selectedKnowledge.value) return;
  loading.value = true;
  try {
    await adminAPI.updateKnowledge(selectedKnowledge.value.id, {
      title: knowledgeForm.value.title,
      section: knowledgeForm.value.section,
      url: knowledgeForm.value.url,
      access_scope: knowledgeForm.value.access_scope,
      text_content: knowledgeForm.value.text_content
    });
    await Promise.all([loadKnowledge(), loadFaqs()]);
    clearKnowledgeSelection();
    setMessage('资料记录已保存并同步到向量库');
  } catch (error) {
    setMessage(error.message || '资料保存失败');
  } finally {
    loading.value = false;
  }
};

const deleteKnowledge = async (item) => {
  askConfirm({
    title: '系统提示',
    body: '确定删除这整条资料记录吗？确认后会删除 MySQL 中的资料记录，并同步删除对应向量库记录。',
    danger: true,
    action: async () => {
      loading.value = true;
      try {
        await adminAPI.deleteKnowledge(item.id);
        await Promise.all([loadKnowledge(), loadFaqs()]);
        if (selectedKnowledge.value?.id === item.id) clearKnowledgeSelection();
        setMessage('资料记录已删除');
      } catch (error) {
        setMessage(error.message || '资料删除失败');
      } finally {
        loading.value = false;
      }
    }
  });
};

const runPreview = async () => {
  loading.value = true;
  previewBlocks.value = [];
  try {
    const result = await adminAPI.crawlPreview(crawlUrl.value, maxPages.value);
    sourceUrl.value = result.source_url;
    previewBlocks.value = result.blocks || [];
    setMessage(`已生成 ${previewBlocks.value.length} 个可编辑文本块`);
  } catch (error) {
    setMessage(error.message || '爬取预览失败');
  } finally {
    loading.value = false;
  }
};

const saveCrawl = async () => {
  if (!previewBlocks.value.length) return;
  loading.value = true;
  try {
    const result = await adminAPI.saveCrawl(sourceUrl.value || crawlUrl.value, accessScope.value, previewBlocks.value);
    setMessage(`已保存 ${result.inserted || 0} 个文本块到 ${result.collection_name}`);
    previewBlocks.value = [];
    await loadKnowledge();
  } catch (error) {
    setMessage(error.message || '保存入库失败');
  } finally {
    loading.value = false;
  }
};

onMounted(async () => {
  loading.value = true;
  try {
    await Promise.all([loadFaqs(), loadKnowledge()]);
    newFaq();
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div class="fixed inset-0 z-50 bg-slate-950/50 backdrop-blur-sm flex items-center justify-center p-4">
    <div class="w-full max-w-6xl h-[88vh] bg-white rounded-lg shadow-2xl border border-slate-200 flex flex-col overflow-hidden">
      <header class="h-14 px-5 border-b flex items-center justify-between">
        <div>
          <h2 class="text-base font-semibold text-slate-900">管理后台</h2>
          <p class="text-xs text-slate-500">FAQ、知识库资料与网页爬取入库</p>
        </div>
        <button class="p-2 rounded hover:bg-slate-100 text-slate-500" @click="emit('close')">
          <X size="18" />
        </button>
      </header>

      <div class="flex flex-1 min-h-0">
        <aside class="w-48 border-r bg-slate-50 p-3 space-y-2">
          <button
            class="w-full flex items-center gap-2 px-3 py-2 rounded text-sm text-left"
            :class="activeTab === 'crawl' ? 'bg-cyan-700 text-white' : 'text-slate-600 hover:bg-white'"
            @click="activeTab = 'crawl'"
          >
            <FilePlus size="16" /> 爬取入库
          </button>
          <button
            class="w-full flex items-center gap-2 px-3 py-2 rounded text-sm text-left"
            :class="activeTab === 'faqs' ? 'bg-cyan-700 text-white' : 'text-slate-600 hover:bg-white'"
            @click="activeTab = 'faqs'"
          >
            <BookOpen size="16" /> FAQ 管理
          </button>
          <button
            class="w-full flex items-center gap-2 px-3 py-2 rounded text-sm text-left"
            :class="activeTab === 'knowledge' ? 'bg-cyan-700 text-white' : 'text-slate-600 hover:bg-white'"
            @click="activeTab = 'knowledge'"
          >
            <Database size="16" /> 资料库
          </button>
        </aside>

        <main class="flex-1 overflow-y-auto p-5 bg-white">
          <div v-if="message" class="mb-4 rounded border border-cyan-100 bg-cyan-50 px-3 py-2 text-sm text-cyan-800">
            {{ message }}
          </div>

          <section v-if="activeTab === 'crawl'" class="space-y-4">
            <div class="grid grid-cols-1 lg:grid-cols-[1fr_150px_180px_auto] gap-3 items-end">
              <label class="space-y-1">
                <span class="text-xs font-semibold text-slate-500">网页入口</span>
                <input v-model="crawlUrl" class="w-full rounded border border-slate-200 px-3 py-2 text-sm" />
              </label>
              <label class="space-y-1">
                <span class="text-xs font-semibold text-slate-500">最大文章页</span>
                <input v-model.number="maxPages" type="number" min="1" max="30" class="w-full rounded border border-slate-200 px-3 py-2 text-sm" />
              </label>
              <label class="space-y-1">
                <span class="text-xs font-semibold text-slate-500">访问范围</span>
                <select v-model="accessScope" class="w-full rounded border border-slate-200 px-3 py-2 text-sm">
                  <option value="public">访客可访问</option>
                  <option value="campus">仅限师生</option>
                </select>
              </label>
              <button
                class="inline-flex items-center justify-center gap-2 rounded bg-cyan-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
                :disabled="loading || !crawlUrl"
                @click="runPreview"
              >
                <RefreshCw size="16" /> 预览
              </button>
            </div>

            <div v-if="previewBlocks.length" class="space-y-4">
              <div class="flex items-center justify-between">
                <div class="text-sm text-slate-500">按文章分类查看并可手动修改文本块</div>
                <button
                  class="inline-flex items-center gap-2 rounded bg-emerald-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
                  :disabled="loading"
                  @click="saveCrawl"
                >
                  <Save size="16" /> 保存入库
                </button>
              </div>

              <div v-for="(items, section) in groupedBlocks" :key="section" class="border rounded-lg overflow-hidden">
                <div class="bg-slate-50 px-3 py-2 text-sm font-semibold text-slate-700">{{ section }} · {{ items.length }}</div>
                <div class="divide-y">
                  <div v-for="block in items" :key="`${block.url}-${block.text.slice(0, 20)}`" class="p-3 space-y-2">
                    <input v-model="block.title" class="w-full rounded border border-slate-200 px-3 py-2 text-sm font-medium" />
                    <input v-model="block.url" class="w-full rounded border border-slate-200 px-3 py-2 text-xs text-cyan-700" />
                    <textarea v-model="block.text" rows="5" class="w-full rounded border border-slate-200 px-3 py-2 text-sm leading-relaxed" />
                  </div>
                </div>
              </div>
            </div>
          </section>

          <section v-else-if="activeTab === 'faqs'" class="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-4">
            <div class="border rounded-lg overflow-hidden">
              <div class="p-3 border-b flex items-center justify-between bg-slate-50">
                <span class="text-sm font-semibold">FAQ 列表</span>
                <button class="text-xs text-cyan-700 hover:underline" @click="newFaq">新增</button>
              </div>
              <div class="max-h-[62vh] overflow-y-auto divide-y">
                <button
                  v-for="faq in faqs"
                  :key="faq.id"
                  class="w-full text-left p-3 text-sm hover:bg-slate-50"
                  :class="selectedFaq?.id === faq.id ? 'bg-cyan-50 text-cyan-800' : 'text-slate-700'"
                  @click="selectFaq(faq)"
                >
                  <div class="font-medium line-clamp-2">{{ faq.question }}</div>
                  <div class="text-xs text-slate-400 mt-1">{{ faq.source || 'FAQ' }}</div>
                </button>
              </div>
            </div>
            <div class="border rounded-lg p-4 space-y-3">
              <label class="block space-y-1">
                <span class="text-xs font-semibold text-slate-500">问题</span>
                <input v-model="faqForm.question" class="w-full rounded border border-slate-200 px-3 py-2 text-sm" />
              </label>
              <label class="block space-y-1">
                <span class="text-xs font-semibold text-slate-500">答案</span>
                <textarea v-model="faqForm.answer" rows="8" class="w-full rounded border border-slate-200 px-3 py-2 text-sm leading-relaxed" />
              </label>
              <label class="block space-y-1">
                <span class="text-xs font-semibold text-slate-500">来源</span>
                <input v-model="faqForm.source" class="w-full rounded border border-slate-200 px-3 py-2 text-sm" />
              </label>
              <label class="block space-y-1">
                <span class="text-xs font-semibold text-slate-500">别名，每行一个</span>
                <textarea v-model="faqForm.aliases" rows="3" class="w-full rounded border border-slate-200 px-3 py-2 text-sm" />
              </label>
              <label class="inline-flex items-center gap-2 text-sm text-slate-600">
                <input v-model="faqForm.is_active" type="checkbox" class="rounded border-slate-300" />
                启用
              </label>
              <div>
                <button class="inline-flex items-center gap-2 rounded bg-cyan-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60" :disabled="loading" @click="saveFaq">
                  <Save size="16" /> 保存 FAQ
                </button>
                <button
                  v-if="selectedFaq"
                  class="ml-2 inline-flex items-center gap-2 rounded border border-red-200 px-4 py-2 text-sm font-semibold text-red-600 hover:bg-red-50 disabled:opacity-60"
                  :disabled="loading"
                  @click="deleteFaq"
                >
                  <Trash2 size="16" /> 删除
                </button>
              </div>
            </div>
          </section>

          <section v-else class="space-y-4">
            <div class="flex items-end gap-3">
              <label class="space-y-1">
                <span class="text-xs font-semibold text-slate-500">集合筛选</span>
                <select v-model="collectionFilter" class="rounded border border-slate-200 px-3 py-2 text-sm">
                  <option value="">全部</option>
                  <option value="rag_standard">访客公开库</option>
                  <option value="rag_knowledge">师生知识库</option>
                  <option value="rag_faq">FAQ</option>
                </select>
              </label>
              <button class="inline-flex items-center gap-2 rounded border border-slate-200 px-4 py-2 text-sm text-slate-700" @click="loadKnowledge">
                <RefreshCw size="16" /> 刷新
              </button>
            </div>

            <div v-if="selectedKnowledge" class="border rounded-lg p-4 space-y-3 bg-slate-50">
              <div class="flex items-center justify-between gap-3">
                <div>
                  <div class="text-sm font-semibold text-slate-800">编辑资料记录</div>
                  <div class="text-xs text-slate-500">{{ selectedKnowledge.collection_name }} · #{{ selectedKnowledge.id }}</div>
                </div>
                <button class="p-2 rounded hover:bg-white text-slate-500" @click="clearKnowledgeSelection">
                  <X size="16" />
                </button>
              </div>
              <div class="grid grid-cols-1 lg:grid-cols-[1fr_180px_180px] gap-3">
                <label class="space-y-1">
                  <span class="text-xs font-semibold text-slate-500">标题 / 问题</span>
                  <input v-model="knowledgeForm.title" class="w-full rounded border border-slate-200 px-3 py-2 text-sm" />
                </label>
                <label class="space-y-1">
                  <span class="text-xs font-semibold text-slate-500">分类</span>
                  <input v-model="knowledgeForm.section" class="w-full rounded border border-slate-200 px-3 py-2 text-sm" />
                </label>
                <label class="space-y-1">
                  <span class="text-xs font-semibold text-slate-500">访问范围</span>
                  <select v-model="knowledgeForm.access_scope" class="w-full rounded border border-slate-200 px-3 py-2 text-sm">
                    <option value="public">访客可访问</option>
                    <option value="campus">仅限师生</option>
                  </select>
                </label>
              </div>
              <label class="block space-y-1">
                <span class="text-xs font-semibold text-slate-500">来源 URL</span>
                <input v-model="knowledgeForm.url" class="w-full rounded border border-slate-200 px-3 py-2 text-xs text-cyan-700" />
              </label>
              <label class="block space-y-1">
                <span class="text-xs font-semibold text-slate-500">资料内容 / 答案</span>
                <textarea v-model="knowledgeForm.text_content" rows="8" class="w-full rounded border border-slate-200 px-3 py-2 text-sm leading-relaxed" />
              </label>
              <div class="flex items-center gap-2">
                <button class="inline-flex items-center gap-2 rounded bg-cyan-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60" :disabled="loading" @click="saveKnowledge">
                  <Save size="16" /> 保存记录
                </button>
                <button class="inline-flex items-center gap-2 rounded border border-red-200 px-4 py-2 text-sm font-semibold text-red-600 hover:bg-red-50 disabled:opacity-60" :disabled="loading" @click="deleteKnowledge(selectedKnowledge)">
                  <Trash2 size="16" /> 删除整条
                </button>
              </div>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
              <div v-for="item in knowledge" :key="item.id" class="rounded-lg border border-slate-200 p-3 space-y-2">
                <div class="flex items-start justify-between gap-3">
                  <div class="font-semibold text-sm text-slate-800">{{ item.title || '资料块' }}</div>
                  <div class="flex items-center gap-2">
                    <span class="text-[11px] rounded bg-slate-100 px-2 py-0.5 text-slate-500">{{ item.collection_name || 'unknown' }}</span>
                    <button class="p-1.5 rounded text-slate-500 hover:bg-slate-100" title="编辑" @click="selectKnowledge(item)">
                      <Pencil size="14" />
                    </button>
                    <button class="p-1.5 rounded text-red-500 hover:bg-red-50" title="删除" @click="deleteKnowledge(item)">
                      <Trash2 size="14" />
                    </button>
                  </div>
                </div>
                <a :href="item.url" target="_blank" rel="noopener noreferrer" class="block text-xs text-cyan-700 truncate">{{ item.url }}</a>
                <p class="text-xs leading-relaxed text-slate-600">{{ item.text_content || item.text_preview }}</p>
              </div>
            </div>
          </section>
        </main>
      </div>

      <div v-if="confirmDialog.visible" class="absolute inset-0 z-10 flex items-center justify-center bg-slate-950/35 px-4">
        <div class="w-full max-w-sm rounded-lg border border-slate-200 bg-white shadow-2xl">
          <div class="border-b px-4 py-3">
            <h3 class="text-base font-semibold text-slate-900">{{ confirmDialog.title }}</h3>
          </div>
          <div class="px-4 py-4">
            <p class="text-sm leading-6 text-slate-600">{{ confirmDialog.body }}</p>
          </div>
          <div class="flex justify-end gap-2 border-t bg-slate-50 px-4 py-3">
            <button
              class="rounded border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-white"
              :disabled="loading"
              @click="closeConfirm"
            >
              取消
            </button>
            <button
              class="rounded px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
              :class="confirmDialog.danger ? 'bg-red-600 hover:bg-red-700' : 'bg-cyan-700 hover:bg-cyan-800'"
              :disabled="loading"
              @click="confirmAction"
            >
              确定
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
