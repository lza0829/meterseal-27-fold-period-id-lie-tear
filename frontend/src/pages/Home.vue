<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 批状态, 账期状态, 标 } from '../labels'
const data = ref(null)
const err = ref('')
onMounted(async () => {
  try { data.value = await api.overview() } catch (e) { err.value = e.message }
})

const receiptPeriod = ref('')
onMounted(async () => {
  try {
    const r = await api.get('/api/period-receipt')
    receiptPeriod.value = (r && r.receipt_period_id) || ''
  } catch (_) {}
})
</script>
<template>
  <div>
    <h1>总览</h1>
    <p class="sub">当前账期估抄、实抄、对齐与封印状态；按账期计数与按期列表同口径</p>
    <p v-if="err" class="msg err">{{ err }}</p>
    <div class="grid" v-if="data">
      <div class="stat">当前账期<b>{{ data.current_period }}</b></div>
      <div class="stat">开放账期<b>{{ data.open_periods }}</b></div>
      <div class="stat">已封印账期<b>{{ data.sealed_periods }}</b></div>
      <div class="stat">表具数<b>{{ data.meter_count }}</b></div>
      <div class="stat">未决差额<b>{{ data.pending_variances }}</b></div>
    </div>
    <div class="panel" v-if="data">
      <h3>按账期计数</h3>
      <table>
        <thead><tr><th>账期</th><th>状态</th><th>估抄</th><th>实抄</th><th>其中折期</th><th>未决差额</th></tr></thead>
        <tbody>
          <tr v-for="p in data.period_counts" :key="p.period_id">
            <td>{{ p.period_id }}</td>
            <td><span class="tag" :class="p.status">{{ 标(账期状态, p.status) }}</span></td>
            <td>{{ p.estimates }}</td>
            <td>{{ p.actuals }}</td>
            <td>{{ p.folded_actuals }}</td>
            <td>{{ p.pending_variances }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel" v-if="data">
      <h3>最近对账批</h3>
      <table>
        <thead><tr><th>批号</th><th>账期</th><th>匹配</th><th>差额</th><th>折期笔数</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="b in data.recent_batches" :key="b.batch_id">
            <td>{{ b.batch_id }}</td>
            <td>{{ b.period_id }}</td>
            <td>{{ b.matched_count }}</td>
            <td>{{ b.variance_count }}</td>
            <td>{{ b.folded_count ?? 0 }}</td>
            <td><span class="tag" :class="b.status">{{ 标(批状态, b.status) }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
