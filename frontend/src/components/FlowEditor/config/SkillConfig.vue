<script setup lang="ts">
import { ref, watch } from 'vue'
import { skillApi } from '@/api/skill'
import type { SkillConfig } from './types'

const props = defineProps<{
  config: SkillConfig
  nodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: SkillConfig): void
  // (e: 'update:label', value: string): void
}>()

const skills = ref<{ id: number; name: string }[]>([])
const loading = ref(false)

async function loadSkills(): Promise<void> {
  if (skills.value.length || loading.value) return
  loading.value = true
  try {
    const res = await skillApi.list()
    if (res.data.code === 1 && res.data.data) {
      skills.value = res.data.data
    }
  } catch {
    skills.value = []
  } finally {
    loading.value = false
  }
}
loadSkills()

const localConfig = ref<SkillConfig>({ skill_ids: [] })

watch(
  () => props.config,
  newConfig => {
    localConfig.value = {
      skill_ids: newConfig.skill_ids || []
      // skill_names: newConfig.skill_names || []
    }
  },
  { deep: true, immediate: true }
)

function updateConfig(): void {
  // const selectedSkills = skills.value.filter(s => localConfig.value.skill_ids.includes(s.id))
  // localConfig.value.skill_names = selectedSkills.map(s => s.name)
  emit('update:config', { ...localConfig.value })
  // emit('update:label', selectedSkills.map(s => s.name).join(', ') || '')
}
</script>

<template>
  <div class="skill-config">
    <div class="config-section">
      <div class="section-title">技能配置</div>
      <el-form label-width="80px" size="small">
        <el-form-item label="选择技能">
          <el-select
            v-model="localConfig.skill_ids"
            placeholder="选择技能（可多选）"
            style="width: 100%"
            :loading="loading"
            multiple
            collapse-tags
            collapse-tags-tooltip
            @change="updateConfig"
          >
            <el-option
              v-for="skill in skills"
              :key="skill.id"
              :label="skill.name"
              :value="skill.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<style scoped>
.config-section {
  margin-bottom: 16px;
}

.section-title {
  font-size: 13px;
  color: #606266;
  margin-bottom: 12px;
  font-weight: 500;
}
</style>
