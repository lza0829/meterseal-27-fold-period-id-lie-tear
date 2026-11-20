<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { 批状态, 命运, 折期规则, 标 } from '../labels'
const periodId = ref('2026-10')
const batch = ref(null)
const vars = ref([])
const msg = ref('')
const err = ref('')
const declaredPeriods = computed(() => {
  const d = batch.value?.declared_periods
  if (!d) return []
  return Array.isArray(d) ? d : JSON.parse(d)
})
async function align() {
  err.value = ''; msg.value = ''
  try {
    batch.value = await api.align(periodId.value)
    vars.value = await api.variances({ batch_id: batch.value.batch_id })
    msg.value = `对齐完成：匹配 ${batch.value.matched_count}，差额 ${batch.value.variance_count}，折期 ${batch.value.folded_count} 笔`
  } catch (e) { err.value = e.message }
}
async function fate(id, f) {
  err.value = ''
  try {
    await api.setFate(id, f)
    vars.value = await api.variances({ batch_id: batch.value.batch_id })
  } catch (e) { err.value = e.message }
}
onMounted(async () => {
  const bs = await api.batches(periodId.value)
  if (bs[0]) {
    batch.value = bs[0]
    vars.value = await api.variances({ batch_id: bs[0].batch_id })
  }
})
</script>
<template>
  <div>
    <h1>对齐差额</h1>
    <p class="sub">按表号与落期账期对齐估抄、实抄；批次快照记录折期口径</p>
    <div class="row">
      <label>账期<input v-model="periodId" /></label>
      <button @click="align">跑对齐</button>
    </div>
    <p class="msg ok" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>
    <div class="panel" v-if="batch">
      <p>
        批号 {{ batch.batch_id }} · 指纹 {{ batch.fingerprint }} ·
        <span class="tag" :class="batch.status">{{ 标(批状态, batch.status) }}</span>
      </p>
      <p>
        折期规则 {{ 标(折期规则, batch.fold_rule) }} · 折期笔数 {{ batch.folded_count ?? 0 }} ·
        原申报账期 {{ declaredPeriods.length ? declaredPeriods.join('、') : '—' }}
      </p>
      <table>
        <thead><tr><th>差额编号</th><th>表号</th><th>估抄</th><th>实抄</th><th>差值</th><th>命运</th><th>处置</th></tr></thead>
        <tbody>
          <tr v-for="v in vars" :key="v.variance_id">
            <td>{{ v.variance_id }}</td>
            <td>{{ v.meter_id }}</td>
            <td>{{ v.estimate_qty }}</td>
            <td>{{ v.actual_qty }}</td>
            <td>{{ v.delta }}</td>
            <td>{{ 标(命运, v.fate) }}</td>
            <td>
              <button class="secondary" @click="fate(v.variance_id, 'accept_short')">认短</button>
              <button class="secondary" @click="fate(v.variance_id, 'accept_over')">认溢</button>
              <button class="warn" @click="fate(v.variance_id, 'quarantine')">隔离</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
