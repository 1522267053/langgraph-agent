<script setup lang="ts">
// AgendaConfig is empty - no imports needed
</script>

<template>
  <div class="agenda-config">
    <div class="config-section">
      <div class="section-title">日程管理</div>
      <div class="config-hint">
        <el-text size="small" type="info">
          日程管理节点需通过工具连接到LLM节点使用。
          <br />
          LLM将获得 agenda_create、agenda_list、agenda_update、agenda_delete 工具，可自动创建和管理日程。
          <br />
          适用于帮用户安排时间、设置提醒和管理日程。
        </el-text>
      </div>
    </div>
  </div>
</template>

<style scoped>
@import './config-styles.css';
</style>
