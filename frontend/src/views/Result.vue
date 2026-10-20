<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { message } from "ant-design-vue";

import AmapTripMap from "../components/AmapTripMap.vue";
import {
  editTrip,
  fetchWeatherForecast,
  getMarkdownExportUrl,
  getPdfExportUrl,
  saveTrip,
} from "../services/api";
import type { Itinerary, WeatherForecastResponse } from "../types";

const props = defineProps<{
  itinerary: Itinerary | null;
}>();

const emit = defineEmits<{
  backHome: [];
  viewHistory: [];
  updated: [itinerary: Itinerary];
}>();

const saving = ref(false);
const exportingPdf = ref(false);
const exportingMarkdown = ref(false);
const editing = ref(false);
const editScope = ref("day_1");
const editInstruction = ref("这一天节奏更轻松一点，减少固定安排。");
const weatherLoading = ref(false);
const weatherError = ref("");
const weather = ref<WeatherForecastResponse | null>(null);
const mapModalOpen = ref(false);

function formatShortDate(dateText?: string | null): string {
  if (!dateText) {
    return "待定";
  }

  const parts = dateText.split("-");
  if (parts.length !== 3) {
    return dateText;
  }

  return `${parts[1]}-${parts[2]}`;
}

function formatWeatherDate(dateText?: string | null, week?: string | null): string {
  const weekdayMap: Record<string, string> = {
    "1": "周一",
    "2": "周二",
    "3": "周三",
    "4": "周四",
    "5": "周五",
    "6": "周六",
    "7": "周日",
  };
  const weekday = week ? weekdayMap[week] || `周${week}` : "";
  return [formatShortDate(dateText), weekday].filter(Boolean).join(" ");
}

const budgetItems = computed(() => {
  if (!props.itinerary) {
    return [];
  }

  const budget = props.itinerary.budget_breakdown;
  return [
    { label: "景点门票", value: `¥${budget.tickets.toFixed(0)}` },
    { label: "酒店住宿", value: `¥${budget.hotel.toFixed(0)}` },
    { label: "餐饮费用", value: `¥${budget.meals.toFixed(0)}` },
    { label: "交通费用", value: `¥${budget.transport.toFixed(0)}` },
  ];
});

const dayBudgetItems = computed(() => {
  if (!props.itinerary) {
    return [];
  }

  return props.itinerary.days.map((day) => {
    const tickets = day.spots.reduce((sum, spot) => sum + (spot.estimated_cost ?? 0), 0);
    const meals = day.meals.reduce((sum, meal) => sum + (meal.estimated_cost ?? 0), 0);
    const transport = day.transport.reduce((sum, item) => sum + (item.estimated_cost ?? 0), 0);
    const hotel = day.hotel?.estimated_cost ?? 0;
    const total = tickets + meals + transport + hotel;

    return {
      key: day.day_index,
      title: `第${day.day_index}天`,
      subtitle: day.theme || "未命名主题",
      tickets,
      meals,
      transport,
      hotel,
      total,
    };
  });
});

const sourceItems = computed(() => {
  return (props.itinerary?.source_notes || []).flatMap((note, index) => {
    const url = note.match(/https?:\/\/[^\s；;)]+/)?.[0] || "";
    if (url) {
      const title = note.replace(/^在线资料来源：/, "").split(" — ")[0].trim();
      const detail = note.replace(title, "").replace(url, "").replace(/^[\s—；;]+/, "").trim();
      return [{ key: `${index}-${url}`, title: title || url, detail, url, kind: "网页来源" }];
    }

    const localMatch = note.match(/^\[来源:\s*([^|\]]+)\s*\|\s*标题:\s*([^\]]+)\]/);
    if (localMatch) {
      const fileName = localMatch[1].trim().replace(/\.md$/i, "");
      const title = localMatch[2].trim();
      return [{
        key: `${index}-${fileName}-${title}`,
        title,
        detail: `本地攻略 · ${fileName}`,
        url: "",
        kind: "本地资料",
      }];
    }

    if (note.startsWith("本次未") || note.startsWith("[在线检索状态]")) {
      return [{ key: `${index}-status`, title: note, detail: "", url: "", kind: "检索状态" }];
    }
    return [];
  });
});

const agentTrace = computed(() => props.itinerary?.agent_trace);
const traceMessages = computed(() => agentTrace.value?.messages || []);
const traceToolCalls = computed(() => agentTrace.value?.tool_calls || []);
const primarySource = computed(() => sourceItems.value[0] || null);
const additionalSourceCount = computed(() => Math.max(0, sourceItems.value.length - 1));
const firstTraceToolCall = computed(() => traceToolCalls.value[0] || null);
const additionalTraceToolCallCount = computed(() => Math.max(0, traceToolCalls.value.length - 1));
const firstTraceMessage = computed(() => traceMessages.value[0] || null);
const additionalTraceMessageCount = computed(() => Math.max(0, traceMessages.value.length - 1));

const mapPoints = computed(() => {
  if (!props.itinerary) {
    return [];
  }

  return props.itinerary.days.flatMap((day) =>
    day.spots.map((spot) => ({
      key: `${day.day_index}-${spot.name}`,
      dayIndex: day.day_index,
      date: day.date || "待定",
      theme: day.theme || "未命名主题",
      name: spot.name,
      address: spot.address || spot.location || "待补充",
      latitude: spot.latitude,
      longitude: spot.longitude,
      poiId: spot.poi_id,
      imageUrl: spot.image_url,
      description: spot.description || "暂无说明",
    }))
  );
});

const technicalTipKeywords = [
  "LLM",
  "RAG",
  "LangChain",
  "Chroma",
  "演示",
  "测试",
  "规则",
  "模型",
  "源码",
];

const rainWeatherKeywords = ["雨", "阵雨", "雷阵雨", "小雨", "中雨", "大雨"];
const sunnyTipKeywords = ["防晒", "太阳", "日照", "晒"];

const weatherText = computed(() => {
  if (!weather.value) {
    return "";
  }

  return weather.value.days
    .map((day) => `${day.day_weather || ""}${day.night_weather || ""}`)
    .join(" ");
});

const hasRainyWeather = computed(() => {
  return rainWeatherKeywords.some((keyword) => weatherText.value.includes(keyword));
});

const displayTips = computed(() => {
  if (!props.itinerary) {
    return [];
  }

  const tips = props.itinerary.tips
    .map((tip) => tip.trim())
    .filter(Boolean)
    .filter((tip) => !technicalTipKeywords.some((keyword) => tip.includes(keyword)));

  const weatherAwareTips = hasRainyWeather.value
    ? tips.filter((tip) => !sunnyTipKeywords.some((keyword) => tip.includes(keyword)))
    : tips;

  if (hasRainyWeather.value) {
    weatherAwareTips.push("天气可能有雨，建议随身带伞或轻便雨衣。");
    weatherAwareTips.push("阴雨天路面湿滑，洱海边和古镇石板路建议穿防滑鞋。");
  }

  const uniqueTips = Array.from(new Set(weatherAwareTips));
  if (uniqueTips.length) {
    return uniqueTips;
  }

  return [
    `建议根据${props.itinerary.destination}当天实时天气准备雨具或薄外套。`,
    "古镇、生态廊道和石板路更适合慢慢走，鞋子尽量选择舒适防滑的款式。",
  ];
});

function buildVisibleItinerary(): Itinerary | null {
  if (!props.itinerary) {
    return null;
  }

  return {
    ...props.itinerary,
    tips: displayTips.value,
  };
}

async function loadWeather() {
  if (!props.itinerary?.destination) {
    weather.value = null;
    return;
  }

  weatherLoading.value = true;
  weatherError.value = "";
  try {
    weather.value = await fetchWeatherForecast(props.itinerary.destination);
  } catch (error) {
    console.error(error);
    weather.value = null;
    weatherError.value = "天气信息加载失败。";
  } finally {
    weatherLoading.value = false;
  }
}

watch(
  () => props.itinerary?.destination,
  () => {
    void loadWeather();
  },
  { immediate: true }
);

watch(
  () => props.itinerary?.trip_id,
  () => {
    const firstDay = props.itinerary?.days[0];
    editScope.value = firstDay ? `day_${firstDay.day_index}` : "day_1";
  },
  { immediate: true }
);

async function openPdfExport() {
  const itineraryToExport = buildVisibleItinerary();
  if (!itineraryToExport) {
    return;
  }

  const exportWindow = window.open("about:blank", "_blank");
  exportingPdf.value = true;
  try {
    await saveTrip(itineraryToExport);
    const exportUrl = getPdfExportUrl(itineraryToExport.trip_id);
    if (exportWindow) {
      exportWindow.location.href = exportUrl;
    } else {
      window.location.href = exportUrl;
    }
  } catch (error) {
    console.error(error);
    exportWindow?.close();
    message.error("导出 PDF 前同步当前行程失败。");
  } finally {
    exportingPdf.value = false;
  }
}

async function openMarkdownExport() {
  const itineraryToExport = buildVisibleItinerary();
  if (!itineraryToExport) {
    return;
  }

  const exportWindow = window.open("about:blank", "_blank");
  exportingMarkdown.value = true;
  try {
    await saveTrip(itineraryToExport);
    const exportUrl = getMarkdownExportUrl(itineraryToExport.trip_id);
    if (exportWindow) {
      exportWindow.location.href = exportUrl;
    } else {
      window.location.href = exportUrl;
    }
  } catch (error) {
    console.error(error);
    exportWindow?.close();
    message.error("导出 Markdown 前同步当前行程失败。");
  } finally {
    exportingMarkdown.value = false;
  }
}

async function handleSave() {
  const itineraryToSave = buildVisibleItinerary();
  if (!itineraryToSave) {
    return;
  }

  saving.value = true;
  try {
    await saveTrip(itineraryToSave);
    message.success("行程已保存，可以去历史列表查看。");
  } catch (error) {
    console.error(error);
    message.error("保存行程失败。");
  } finally {
    saving.value = false;
  }
}

async function handleEdit() {
  if (!props.itinerary) {
    return;
  }

  const instruction = editInstruction.value.trim();
  if (!instruction) {
    message.warning("请先输入想如何调整行程。");
    return;
  }

  editing.value = true;
  try {
    const updatedItinerary = await editTrip({
      trip_id: props.itinerary.trip_id,
      current_itinerary: props.itinerary,
      user_instruction: instruction,
      edit_scope: editScope.value,
      preserve_constraints: ["保留预算结构", "保留目的地和旅行日期"],
    });
    emit("updated", updatedItinerary);
    const decisionTip = updatedItinerary.tips.find((tip) => tip.startsWith("本次未修改行程："));
    if (decisionTip) message.info("Agent 建议保留原行程，原因和替代建议已显示在旅行提示中。");
    else message.success("Agent 已完成评估并更新行程。");
  } catch (error) {
    console.error(error);
    message.error("智能调整失败，请稍后再试。");
  } finally {
    editing.value = false;
  }
}
</script>

<template>
  <section v-if="itinerary" class="result-page">
    <aside class="sidebar-card">
      <div class="sidebar-card__title">行程导航</div>
      <ul class="sidebar-list">
        <li>行程概览</li>
        <li>预算明细</li>
        <li>按天花费</li>
        <li>智能调整</li>
        <li>景点地图</li>
        <li>天气信息</li>
        <li>点位明细</li>
        <li>每日行程</li>
      </ul>

      <div class="sidebar-actions">
        <button class="back-button" @click="$emit('backHome')">返回规划页</button>
        <button class="save-button" :disabled="saving" @click="handleSave">
          {{ saving ? "保存中..." : "保存行程" }}
        </button>
        <button class="history-button" @click="$emit('viewHistory')">历史列表</button>
        <button class="export-button" :disabled="exportingPdf" @click="openPdfExport">
          {{ exportingPdf ? "准备 PDF..." : "导出 PDF" }}
        </button>
        <button
          class="export-button export-button--light"
          :disabled="exportingMarkdown"
          @click="openMarkdownExport"
        >
          {{ exportingMarkdown ? "准备中..." : "导出 Markdown" }}
        </button>
      </div>
    </aside>

    <div class="result-grid">
      <section class="result-card">
        <div class="result-card__title">{{ itinerary.destination }}旅行计划</div>
        <div class="info-row"><strong>行程 ID：</strong>{{ itinerary.trip_id }}</div>
        <div class="info-row">
          <strong>日期：</strong>
          {{ itinerary.days[0]?.date || "待定" }} 至
          {{ itinerary.days[itinerary.days.length - 1]?.date || "待定" }}
        </div>
        <div class="info-row"><strong>地点：</strong>{{ itinerary.destination }}</div>
        <div class="info-row summary-text">{{ itinerary.summary }}</div>
        <div v-if="displayTips.length" class="overview-tips">
          <div class="overview-tips__title">旅行提示</div>
          <ul>
            <li v-for="tip in displayTips" :key="tip">{{ tip }}</li>
          </ul>
        </div>
      </section>

      <section v-if="sourceItems.length" class="result-card result-card--full">
        <div class="result-card__title">参考资料</div>
        <p class="source-intro">行程参考了以下资料。需要查看完整来源时可展开。</p>
        <ul class="source-list source-list--primary">
          <li v-if="primarySource" :key="primarySource.key" class="source-item">
            <span class="source-kind">{{ primarySource.kind }}</span>
            <a v-if="primarySource.url" :href="primarySource.url" target="_blank" rel="noopener noreferrer" class="source-link">
              {{ primarySource.title || primarySource.url }}
            </a>
            <span v-else class="source-text">{{ primarySource.title }}</span>
            <span v-if="primarySource.detail" class="source-detail">{{ primarySource.detail }}</span>
          </li>
        </ul>
        <details v-if="additionalSourceCount" class="progressive-disclosure source-disclosure">
          <summary>展开其余 {{ additionalSourceCount }} 条资料</summary>
          <ul class="source-list">
            <li v-for="source in sourceItems.slice(1)" :key="source.key" class="source-item">
              <span class="source-kind">{{ source.kind }}</span>
              <a v-if="source.url" :href="source.url" target="_blank" rel="noopener noreferrer" class="source-link">
                {{ source.title || source.url }}
              </a>
              <span v-else class="source-text">{{ source.title }}</span>
              <span v-if="source.detail" class="source-detail">{{ source.detail }}</span>
            </li>
          </ul>
        </details>
      </section>

      <section v-if="agentTrace" class="result-card result-card--full agent-trace-card">
        <div class="result-card__title">Agent 执行记录</div>
        <details class="progressive-disclosure trace-disclosure">
          <summary>展开执行详情 · {{ traceToolCalls.length }} 次工具调用 · {{ traceMessages.length }} 条 messages</summary>
          <p class="source-intro">
            展示 Harness 主动检索、模型发出的工具调用及本轮实际传递的 messages；不包含模型未输出的内部思考过程。
          </p>
          <details class="trace-details">
            <summary>工具调用记录（{{ traceToolCalls.length }} 次）</summary>
            <div v-if="firstTraceToolCall" class="trace-call-list">
              <article class="trace-call">
                <div class="trace-call__heading">
                  <strong>{{ firstTraceToolCall.name }}</strong>
                  <span>{{ firstTraceToolCall.origin === "agent" ? "模型 tool_call" : "Harness 主动检索" }}</span>
                  <span>状态：{{ firstTraceToolCall.status }}</span>
                  <span v-if="firstTraceToolCall.tool_call_id">ID：{{ firstTraceToolCall.tool_call_id }}</span>
                </div>
                <details>
                  <summary>参数与工具结果</summary>
                  <h4>Arguments</h4>
                  <pre>{{ JSON.stringify(firstTraceToolCall.arguments, null, 2) }}</pre>
                  <h4>Tool Result</h4>
                  <pre>{{ firstTraceToolCall.result }}</pre>
                </details>
              </article>
              <details v-if="additionalTraceToolCallCount" class="progressive-disclosure nested-disclosure">
                <summary>展开其余 {{ additionalTraceToolCallCount }} 次调用</summary>
                <article v-for="(call, index) in traceToolCalls.slice(1)" :key="`${call.origin}-${call.step}-${index}`" class="trace-call">
                  <div class="trace-call__heading">
                    <strong>{{ call.name }}</strong>
                    <span>{{ call.origin === "agent" ? "模型 tool_call" : "Harness 主动检索" }}</span>
                    <span>状态：{{ call.status }}</span>
                    <span v-if="call.tool_call_id">ID：{{ call.tool_call_id }}</span>
                  </div>
                  <details>
                    <summary>参数与工具结果</summary>
                    <h4>Arguments</h4>
                    <pre>{{ JSON.stringify(call.arguments, null, 2) }}</pre>
                    <h4>Tool Result</h4>
                    <pre>{{ call.result }}</pre>
                  </details>
                </article>
              </details>
            </div>
            <p v-else class="trace-empty">本轮没有执行工具调用。</p>
          </details>
          <details class="trace-details">
            <summary>完整 messages（{{ traceMessages.length }} 条）</summary>
            <div v-if="firstTraceMessage" class="trace-message-list">
              <article class="trace-message">
                <div class="trace-message__role">{{ String(firstTraceMessage.role || "message") }}</div>
                <pre>{{ JSON.stringify(firstTraceMessage, null, 2) }}</pre>
              </article>
              <details v-if="additionalTraceMessageCount" class="progressive-disclosure nested-disclosure">
                <summary>展开其余 {{ additionalTraceMessageCount }} 条 messages</summary>
                <article v-for="(item, index) in traceMessages.slice(1)" :key="index + 1" class="trace-message">
                  <div class="trace-message__role">{{ String(item.role || "message") }}</div>
                  <pre>{{ JSON.stringify(item, null, 2) }}</pre>
                </article>
              </details>
            </div>
            <p v-else class="trace-empty">本轮没有 messages。</p>
          </details>
          <p v-if="agentTrace.error" class="trace-error">{{ agentTrace.error }}</p>
        </details>
      </section>

      <section class="result-card">
        <div class="result-card__title">预算明细</div>
        <div class="budget-grid">
          <div v-for="item in budgetItems" :key="item.label" class="budget-box">
            <div class="budget-box__label">{{ item.label }}</div>
            <div class="budget-box__value">{{ item.value }}</div>
          </div>
        </div>
        <div class="budget-total">
          <span>预估总费用</span>
          <strong>¥{{ itinerary.estimated_budget.toFixed(0) }}</strong>
        </div>
      </section>

      <section class="result-card result-card--map">
        <div class="result-card__title">景点地图</div>
        <p class="map-preview-note">查看每日景点分布与路线</p>
        <button class="map-open-button" type="button" @click="mapModalOpen = true">
          <span class="map-open-button__icon" aria-hidden="true">⌖</span>
          <span>展开地图</span>
        </button>
      </section>

      <section class="result-card result-card--weather">
        <div class="result-card__title">天气信息</div>

        <div v-if="weatherLoading" class="weather-state">正在加载天气信息...</div>
        <div v-else-if="weatherError" class="weather-state">{{ weatherError }}</div>
        <div v-else-if="weather" class="weather-grid">
          <article
            v-for="day in weather.days"
            :key="`${day.date}-${day.week}`"
            class="weather-card"
          >
            <div class="weather-card__date">
              {{ formatWeatherDate(day.date, day.week) }}
            </div>
            <div class="weather-card__temp">
              {{ day.day_temp || "-" }}° / {{ day.night_temp || "-" }}°
            </div>
            <div class="weather-card__desc">白天：{{ day.day_weather || "未知" }}</div>
            <div class="weather-card__desc">夜间：{{ day.night_weather || "未知" }}</div>
          </article>
        </div>
        <div v-else class="weather-state">暂无天气信息。</div>
      </section>

      <section class="result-card result-card--full">
        <div class="result-card__title">按天花费</div>
        <div class="day-budget-grid">
          <article
            v-for="item in dayBudgetItems"
            :key="item.key"
            class="day-budget-card"
          >
            <div class="day-budget-card__header">
              <span>{{ item.title }}</span>
              <span>{{ item.subtitle }}</span>
            </div>
            <div class="day-budget-card__body">
              <div class="day-budget-row">
                <span>门票</span>
                <strong>¥{{ item.tickets.toFixed(0) }}</strong>
              </div>
              <div class="day-budget-row">
                <span>餐饮</span>
                <strong>¥{{ item.meals.toFixed(0) }}</strong>
              </div>
              <div class="day-budget-row">
                <span>交通</span>
                <strong>¥{{ item.transport.toFixed(0) }}</strong>
              </div>
              <div class="day-budget-row">
                <span>住宿</span>
                <strong>¥{{ item.hotel.toFixed(0) }}</strong>
              </div>
              <div class="day-budget-row day-budget-row--total">
                <span>当日合计</span>
                <strong>¥{{ item.total.toFixed(0) }}</strong>
              </div>
            </div>
          </article>
        </div>
      </section>

      <section class="result-card result-card--full">
        <div class="result-card__title">地图点位明细</div>
        <div class="point-grid">
          <article v-for="point in mapPoints" :key="point.key" class="point-card">
            <div class="point-card__header">
              <span>第{{ point.dayIndex }}天 · {{ point.name }}</span>
              <span>{{ formatShortDate(point.date) }}</span>
            </div>

            <div class="point-card__body">
              <div
                v-if="point.imageUrl"
                class="point-card__image"
                :style="{ backgroundImage: `url(${point.imageUrl})` }"
              ></div>
              <div v-else class="point-card__image point-card__image--empty">
                暂无景点图片
              </div>
              <div class="point-card__line">
                <strong>主题：</strong>
                <span>{{ point.theme }}</span>
              </div>
              <div class="point-card__line">
                <strong>地址：</strong>
                <span>{{ point.address }}</span>
              </div>
              <div class="point-card__desc">{{ point.description }}</div>
            </div>
          </article>
        </div>
      </section>

      <section class="result-card result-card--full">
        <div class="result-card__title">每日行程</div>
        <div class="day-list">
          <details
            v-for="day in itinerary.days"
            :key="day.day_index"
            class="day-card"
            :open="day.day_index === 1"
          >
            <summary class="day-card__header">
              <span>第{{ day.day_index }}天 · {{ day.theme || "未命名主题" }}</span>
              <span class="day-card__meta">{{ formatShortDate(day.date) }}</span>
            </summary>

            <div class="day-card__body">
              <div class="day-card__section">
                <strong>主要景点：</strong>
                <span>{{ day.spots[0]?.name || "未安排" }}</span>
              </div>
              <div class="day-card__section">
                <strong>景点地址：</strong>
                <span>{{ day.spots[0]?.address || day.spots[0]?.location || "待补充" }}</span>
              </div>
              <div class="day-card__section">
                <strong>餐饮建议：</strong>
                <span>{{ day.meals[0]?.name || "未安排" }}</span>
              </div>
              <div class="day-card__section">
                <strong>住宿安排：</strong>
                <span>{{ day.hotel?.name || "未安排" }}</span>
              </div>
              <div class="day-card__section">
                <strong>交通信息：</strong>
                <span>
                  {{
                    day.transport[0]?.distance_km != null
                      ? `${day.transport[0].distance_km.toFixed(2)} km / ${day.transport[0].estimated_minutes ?? 0} 分钟`
                      : day.transport[0]?.duration || "待补充"
                  }}
                </span>
              </div>
              <div class="day-card__section">
                <strong>备注：</strong>
                <span>{{ day.notes[day.notes.length - 1] || "无" }}</span>
              </div>
            </div>
          </details>
        </div>
      </section>
    </div>
    <section class="day-edit-box day-edit-box--last">
      <div class="day-edit-box__heading">
        <strong>看完行程后，想做什么调整？</strong>
        <span>告诉 Agent 你的想法。Agent 会先判断是否符合日期、预算和行程条件，再决定是否修改。</span>
      </div>
      <label class="edit-field">
        <span>调整哪一天</span>
        <select v-model="editScope">
          <option v-for="day in itinerary.days" :key="day.day_index" :value="`day_${day.day_index}`">
            第{{ day.day_index }}天 · {{ day.theme || "未命名主题" }}
          </option>
        </select>
      </label>
      <textarea v-model="editInstruction" class="edit-textarea" rows="3" placeholder="例如：少安排一个景点，午餐想吃地道粤菜，下午留出休息时间。"></textarea>
      <div class="day-edit-box__actions">
        <span>如果要求与行程约束冲突，Agent 会保留原计划并解释原因、提供可行替代建议。</span>
        <button class="edit-submit-button" :disabled="editing" @click="handleEdit">
          {{ editing ? "正在评估并调整..." : "发送给 Agent" }}
        </button>
      </div>
    </section>
  </section>

  <Teleport to="body">
    <div
      v-if="mapModalOpen"
      class="map-modal-backdrop"
      role="presentation"
      @click.self="mapModalOpen = false"
      @keydown.esc.window="mapModalOpen = false"
    >
      <section class="map-modal" role="dialog" aria-modal="true" aria-label="景点地图">
        <header class="map-modal__header">
          <div>
            <h2>{{ itinerary?.destination }}景点地图</h2>
            <p>点击地图标记查看景点详情</p>
          </div>
          <button class="map-modal__close" type="button" aria-label="关闭地图" @click="mapModalOpen = false">
            ×
          </button>
        </header>
        <div class="map-modal__body">
          <AmapTripMap v-if="mapModalOpen" :points="mapPoints" />
        </div>
      </section>
    </div>
  </Teleport>

  <section v-if="!itinerary" class="empty-state">
    <div class="empty-state__card">
      <h2>还没有生成结果</h2>
      <p>先回到规划页生成一条 itinerary，结果页就会开始展示真实数据。</p>
      <button class="back-button" @click="$emit('backHome')">返回规划页</button>
    </div>
  </section>
</template>

<style scoped>
.result-page {
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: 22px;
}

.sidebar-card,
.result-card,
.empty-state__card {
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 22px 55px rgba(98, 116, 164, 0.12);
  backdrop-filter: blur(14px);
}

.sidebar-card {
  align-self: start;
  padding: 18px;
}

.sidebar-card__title,
.result-card__title {
  margin-bottom: 14px;
  padding: 12px 14px;
  border-radius: 14px;
  background: linear-gradient(135deg, #6d82de 0%, #8a67cf 100%);
  color: #ffffff;
  font-size: 15px;
  font-weight: 700;
}

.sidebar-list {
  display: grid;
  gap: 12px;
  padding: 0;
  margin: 0 0 18px;
  list-style: none;
  color: #475467;
  font-size: 14px;
}

.sidebar-actions {
  display: grid;
  gap: 10px;
}

.back-button,
.save-button,
.history-button,
.export-button {
  width: 100%;
  border: none;
  border-radius: 14px;
  padding: 12px 16px;
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
}

.back-button {
  background: rgba(109, 130, 222, 0.12);
  color: #5d66c3;
}

.save-button {
  background: linear-gradient(135deg, #7386e0 0%, #8f71d8 100%);
  color: #ffffff;
}

.save-button:disabled {
  opacity: 0.7;
  cursor: wait;
}

.export-button:disabled {
  opacity: 0.7;
  cursor: wait;
}

.history-button {
  background: rgba(79, 70, 229, 0.1);
  color: #5b5bd6;
}

.export-button {
  background: rgba(59, 130, 246, 0.12);
  color: #3568d4;
}

.export-button--light {
  background: rgba(16, 185, 129, 0.12);
  color: #0f8c63;
}

.result-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
}

.result-card {
  padding: 18px;
}

.result-card--map,
.result-card--weather {
  min-height: 220px;
}

.map-preview-note {
  margin: 2px 0 18px;
  color: #667085;
  font-size: 13px;
}

.map-open-button {
  display: flex;
  width: 100%;
  min-height: 112px;
  align-items: center;
  justify-content: center;
  gap: 10px;
  border: 1px solid rgba(98, 116, 164, 0.16);
  border-radius: 18px;
  background:
    linear-gradient(135deg, rgba(109, 130, 222, 0.12), rgba(138, 103, 207, 0.12)),
    repeating-linear-gradient(45deg, #f8faff 0, #f8faff 12px, #f2f5fd 12px, #f2f5fd 24px);
  color: #4356ae;
  font: inherit;
  font-weight: 750;
  cursor: pointer;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.map-open-button:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 22px rgba(98, 116, 164, 0.16);
}

.map-open-button__icon {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  border-radius: 50%;
  background: #fff;
  font-size: 28px;
}

.map-modal-backdrop {
  position: fixed;
  z-index: 2000;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 28px;
  background: rgba(21, 29, 49, 0.58);
  backdrop-filter: blur(5px);
}

.map-modal {
  display: grid;
  width: min(1180px, 96vw);
  height: min(820px, 90vh);
  grid-template-rows: auto minmax(0, 1fr);
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.7);
  border-radius: 24px;
  background: #fff;
  box-shadow: 0 28px 90px rgba(12, 20, 40, 0.34);
}

.map-modal__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 22px;
  border-bottom: 1px solid #eaedf4;
}

.map-modal__header h2 {
  margin: 0;
  color: #344054;
  font-size: 19px;
}

.map-modal__header p {
  margin: 4px 0 0;
  color: #7b8496;
  font-size: 13px;
}

.map-modal__close {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border: 0;
  border-radius: 50%;
  background: #f2f4f8;
  color: #475467;
  font-size: 27px;
  line-height: 1;
  cursor: pointer;
}

.map-modal__body {
  min-height: 0;
  padding: 14px;
}

.map-modal__body :deep(.trip-map),
.map-modal__body :deep(.trip-map__canvas),
.map-modal__body :deep(.trip-map__placeholder) {
  width: 100%;
  height: 100%;
  min-height: 0;
}

.result-card--full {
  grid-column: 1 / -1;
}

.info-row {
  margin-bottom: 10px;
  color: #475467;
  line-height: 1.7;
}

.summary-text {
  margin-top: 14px;
}

.source-intro {
  margin: 0 0 14px;
  color: #667085;
  font-size: 13px;
  line-height: 1.7;
}

.source-list {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.source-list--primary {
  margin-bottom: 10px;
}

.progressive-disclosure {
  border: 1px solid rgba(98, 116, 164, 0.14);
  border-radius: 12px;
  background: #fbfcff;
}

.progressive-disclosure > summary {
  padding: 11px 14px;
  color: #5263ad;
  font-size: 13px;
  font-weight: 650;
  cursor: pointer;
  transition: color 0.15s ease, background 0.15s ease;
}

.progressive-disclosure > summary:hover {
  color: #344aa7;
  background: #f5f7ff;
}

.source-disclosure {
  margin-top: 8px;
}

.source-disclosure > .source-list {
  padding: 0 10px 10px;
}

.trace-disclosure {
  margin-top: 4px;
}

.nested-disclosure {
  margin: 8px 0 0;
  padding: 0 10px 10px;
  background: #f7f8fc;
}

.source-item {
  position: relative;
  display: grid;
  gap: 4px;
  padding: 12px 14px;
  border: 1px solid rgba(98, 116, 164, 0.1);
  border-radius: 14px;
  background: #f8faff;
}

.source-kind {
  width: fit-content;
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(109, 130, 222, 0.1);
  color: #626db3;
  font-size: 11px;
  font-weight: 700;
}

.source-link,
.source-text {
  color: #4457b8;
  font-weight: 700;
  line-height: 1.6;
  overflow-wrap: anywhere;
}

.source-link:hover {
  color: #6d55bd;
  text-decoration: underline;
}

.source-detail {
  color: #7b8496;
  font-size: 12px;
  line-height: 1.5;
}

.agent-trace-card {
  overflow: hidden;
}

.trace-details {
  margin-top: 12px;
  border: 1px solid rgba(98, 116, 164, 0.14);
  border-radius: 12px;
  background: #fbfcff;
}

.trace-details > summary,
.trace-call details > summary {
  padding: 12px 14px;
  color: #4356ae;
  font-weight: 700;
  cursor: pointer;
}

.trace-call-list,
.trace-message-list {
  display: grid;
  gap: 10px;
  margin: 0;
  padding: 0 12px 12px;
  list-style: none;
}

.trace-call,
.trace-message {
  min-width: 0;
  padding: 12px;
  border: 1px solid #e6eaf4;
  border-radius: 10px;
  background: #fff;
}

.trace-call__heading {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  color: #59657d;
  font-size: 12px;
}

.trace-call__heading strong {
  color: #334aa8;
  font-size: 14px;
}

.trace-call h4 {
  margin: 10px 0 6px;
  color: #667085;
  font-size: 12px;
}

.trace-call pre,
.trace-message pre {
  max-height: 380px;
  overflow: auto;
  margin: 0;
  padding: 10px;
  border-radius: 8px;
  background: #f4f6fb;
  color: #344054;
  font-family: Consolas, "Courier New", monospace;
  font-size: 11px;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.trace-message__role {
  margin-bottom: 8px;
  color: #4457b8;
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}

.trace-empty,
.trace-error {
  margin: 0;
  padding: 12px 14px;
  color: #7b8496;
}

.trace-error {
  color: #b42318;
}

.overview-tips {
  margin-top: 18px;
  padding: 14px 16px;
  border-radius: 16px;
  background: linear-gradient(135deg, rgba(109, 130, 222, 0.08), rgba(138, 103, 207, 0.08));
  border: 1px solid rgba(98, 116, 164, 0.08);
}

.overview-tips__title {
  margin-bottom: 8px;
  color: #465467;
  font-weight: 800;
}

.overview-tips ul {
  display: grid;
  gap: 8px;
  margin: 0;
  padding-left: 18px;
  color: #5d6675;
  line-height: 1.7;
}

.budget-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.budget-box {
  padding: 16px;
  border-radius: 16px;
  background: #f8faff;
  border: 1px solid rgba(98, 116, 164, 0.08);
}

.budget-box__label {
  color: #667085;
  font-size: 13px;
}

.budget-box__value {
  margin-top: 10px;
  color: #3b82f6;
  font-size: 22px;
  font-weight: 700;
}

.budget-total {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 14px;
  padding: 16px 18px;
  border-radius: 18px;
  background: linear-gradient(135deg, #7386e0 0%, #8f71d8 100%);
  color: #ffffff;
}

.budget-total strong {
  font-size: 28px;
}

.weather-state {
  color: #667085;
  line-height: 1.8;
}

.weather-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.result-card--weather {
  grid-column: 1 / -1;
  min-width: 0;
}

.weather-card {
  min-width: 0;
}

.day-edit-box--last {
  grid-column: 2 / -1;
  width: 100%;
  margin-top: 18px;
}

.weather-card {
  padding: 14px;
  border-radius: 16px;
  background: #f8faff;
  border: 1px solid rgba(98, 116, 164, 0.08);
}

.weather-card__date {
  color: #465467;
  font-weight: 700;
}

.weather-card__temp {
  margin: 8px 0;
  color: #3b82f6;
  font-size: clamp(17px, 2vw, 24px);
  font-weight: 800;
}

.weather-card__desc {
  color: #667085;
  line-height: 1.7;
}

.edit-panel {
  display: grid;
  gap: 14px;
}

.day-edit-box {
  display: grid;
  gap: 14px;
  margin-top: 18px;
  padding: 18px;
  border: 1px solid rgba(109, 130, 222, 0.16);
  border-radius: 18px;
  background: linear-gradient(135deg, #f8faff, #fbf9ff);
}

.day-edit-box__heading {
  display: grid;
  gap: 5px;
}

.day-edit-box__heading strong {
  color: #4356ae;
  font-size: 16px;
}

.day-edit-box__heading span,
.day-edit-box__actions > span {
  color: #737f93;
  font-size: 12px;
  line-height: 1.6;
}

.day-edit-box__actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.day-edit-box__actions > span {
  flex: 1 1 auto;
}

.day-edit-box__actions .edit-submit-button {
  flex: 0 0 auto;
  min-width: 160px;
  padding: 0 20px;
  white-space: nowrap;
}

.edit-panel__controls {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 150px;
  gap: 12px;
  align-items: end;
}

.edit-field {
  display: grid;
  gap: 8px;
  color: #465467;
  font-weight: 700;
}

.edit-field select,
.edit-textarea {
  width: 100%;
  border: 1px solid rgba(98, 116, 164, 0.18);
  border-radius: 14px;
  background: #fbfcff;
  color: #334155;
  font: inherit;
  outline: none;
}

.edit-field select {
  min-height: 44px;
  padding: 0 14px;
}

.edit-textarea {
  resize: vertical;
  min-height: 92px;
  padding: 12px 14px;
  line-height: 1.7;
}

.edit-field select:focus,
.edit-textarea:focus {
  border-color: rgba(109, 130, 222, 0.65);
  box-shadow: 0 0 0 3px rgba(109, 130, 222, 0.12);
}

.edit-submit-button {
  min-height: 44px;
  border: none;
  border-radius: 14px;
  background: linear-gradient(135deg, #7386e0 0%, #8f71d8 100%);
  color: #ffffff;
  font-weight: 800;
  cursor: pointer;
}

.edit-submit-button:disabled {
  opacity: 0.7;
  cursor: wait;
}

.day-budget-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 14px;
}

.day-budget-card {
  border-radius: 18px;
  overflow: hidden;
  border: 1px solid rgba(98, 116, 164, 0.08);
  background: #fbfcff;
}

.day-budget-card__header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  background: rgba(109, 130, 222, 0.08);
  color: #465467;
  font-weight: 700;
}

.day-budget-card__body {
  display: grid;
  gap: 10px;
  padding: 16px;
}

.day-budget-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: #475467;
}

.day-budget-row--total {
  padding-top: 10px;
  border-top: 1px solid rgba(98, 116, 164, 0.08);
  color: #2f4fa5;
}

.point-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 14px;
}

.point-card {
  border-radius: 18px;
  overflow: hidden;
  border: 1px solid rgba(98, 116, 164, 0.08);
  background: #fbfcff;
}

.point-card__header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  background: rgba(109, 130, 222, 0.08);
  color: #465467;
  font-weight: 700;
}

.point-card__body {
  display: grid;
  gap: 10px;
  padding: 16px;
}

.point-card__image {
  min-height: 150px;
  border-radius: 14px;
  background-position: center;
  background-size: cover;
  background-color: #eef3ff;
}

.point-card__image--empty {
  display: grid;
  place-items: center;
  color: #7b8494;
  font-weight: 700;
  background:
    linear-gradient(135deg, rgba(129, 179, 255, 0.18), rgba(137, 108, 230, 0.15)),
    #f7f9ff;
}

.point-card__line {
  color: #475467;
  line-height: 1.7;
}

.point-card__desc {
  padding-top: 10px;
  border-top: 1px solid rgba(98, 116, 164, 0.08);
  color: #667085;
  line-height: 1.7;
}

.day-list {
  display: grid;
  gap: 12px;
}

.day-card {
  border-radius: 18px;
  border: 1px solid rgba(98, 116, 164, 0.08);
  background: #fbfcff;
  overflow: hidden;
}

.day-card__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  background: rgba(109, 130, 222, 0.08);
  color: #465467;
  font-weight: 700;
  cursor: pointer;
  list-style: none;
}

.day-card__header::-webkit-details-marker {
  display: none;
}

.day-card__header::after {
  content: "展开";
  flex: 0 0 auto;
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(109, 130, 222, 0.12);
  color: #5b5bd6;
  font-size: 12px;
}

.day-card[open] .day-card__header::after {
  content: "收起";
}

.day-card__meta {
  margin-left: auto;
  color: #667085;
  font-size: 13px;
}

.day-card__body {
  display: grid;
  gap: 10px;
  padding: 16px;
}

.day-card__section {
  color: #475467;
  line-height: 1.7;
}

.empty-state {
  display: grid;
  place-items: center;
  min-height: 360px;
}

.empty-state__card {
  max-width: 560px;
  padding: 36px;
  text-align: center;
}

.empty-state__card h2 {
  margin: 0 0 12px;
}

.empty-state__card p {
  margin: 0 0 18px;
  color: #667085;
  line-height: 1.7;
}

@media (max-width: 600px) {
  .map-modal-backdrop {
    padding: 10px;
  }

  .map-modal {
    width: 100%;
    height: min(88vh, 760px);
    border-radius: 18px;
  }

  .map-modal__header {
    padding: 14px 16px;
  }

  .map-modal__body {
    padding: 8px;
  }

  .weather-grid {
    gap: 8px;
  }

  .weather-card {
    padding: 10px;
  }

  .weather-card__desc {
    font-size: 12px;
  }

  .day-edit-box {
    padding: 14px;
  }

  .day-edit-box__actions {
    align-items: stretch;
    flex-direction: column;
  }
}

@media (max-width: 960px) {
  .result-page {
    grid-template-columns: 1fr;
  }

  .result-grid {
    grid-template-columns: 1fr;
  }

  .edit-panel__controls {
    grid-template-columns: 1fr;
  }

  .day-edit-box--last {
    grid-column: 1 / -1;
  }
}
</style>
