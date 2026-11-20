<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import { 回执决策, 装载类别, 标 } from '../labels'
const periodId = ref('2026-10')
const rows = ref([])
const receipts = ref([])
const form = ref({ meter_id: 'M-W-101', period_id: '2026-10', qty: 10, doc_no: '' })
const msg = ref('')
const err = ref('')
async function load() {
  err.value = ''; msg.value = ''
  rows.value = await api.estimates(periodId.value)
  receipts.value = await api.receipts({ kind: 'estimate' })
}
async function submit() {
  err.value = ''; msg.value = ''
  try {
    const body = { ...form.value, doc_no: form.value.doc_no || ('估-' + Date.now()) }
    const r = await api.postEstimate(body)
    msg.value = `估抄已落湖：落期 ${r.period_id}（回执 ${r.receipt_id}）`
    if (r.period_created) msg.value += `；账期 ${r.period_id} 已自动开立`
    await load()
  } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
<template>
  <div>
    <h1>估抄</h1>
    <p class="sub">估抄无发生时刻，按申报账期落期；账期已封印则整笔拒绝</p>
    <div class="row">
      <label>账期筛选<input v-model="periodId" /></label>
      <button class="secondary" @click="load">刷新</button>
    </div>
    <div class="panel">
      <div class="row">
        <label>表号<input v-model="form.meter_id" /></label>
        <label>账期<input v-model="form.period_id" /></label>
        <label>数量<input type="number" step="0.1" v-model.number="form.qty" /></label>
        <label>单号<input v-model="form.doc_no" placeholder="可空自动生成" /></label>
        <button @click="submit">装载估抄</button>
      </div>
      <p class="msg ok" v-if="msg">{{ msg }}</p>
      <p class="msg err" v-if="err">{{ err }}</p>
      <table>
        <thead><tr><th>编号</th><th>表号</th><th>账期</th><th>数量</th><th>单号</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.estimate_id">
            <td>{{ r.estimate_id }}</td>
            <td>{{ r.meter_id }}</td>
            <td>{{ r.period_id }}</td>
            <td>{{ r.qty }}</td>
            <td>{{ r.doc_no }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h3>装载回执（含拒绝）</h3>
      <table>
        <thead><tr><th>回执</th><th>类别</th><th>单号</th><th>申报账期</th><th>落期账期</th><th>决策</th><th>原因</th></tr></thead>
        <tbody>
          <tr v-for="r in receipts" :key="r.receipt_id">
            <td>{{ r.receipt_id }}</td>
            <td>{{ 标(装载类别, r.kind) }}</td>
            <td>{{ r.doc_no }}</td>
            <td>{{ r.declared_period_id }}</td>
            <td>{{ r.landing_period_id || '—' }}</td>
            <td><span class="tag" :class="r.decision">{{ 标(回执决策, r.decision) }}</span></td>
            <td>{{ r.reason || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
