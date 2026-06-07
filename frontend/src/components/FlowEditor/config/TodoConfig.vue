<script setup lang="ts">
// TodoConfig is empty - no imports needed
</script>

<template>
  <div class="todo-config">
    <div class="config-section">
      <div class="section-title">任务计划</div>
      <div class="config-hint">
        <el-text size="small" type="info">
          任务计划节点需通过工具连接到LLM节点使用。
          <br />
          LLM将获得 todowrite、todoread 工具，可自动创建和更新任务列表。
          <br />
          适用于复杂多步骤任务的规划与进度跟踪。
        </el-text>
      </div>
    </div>
  </div>
</template>

<style scoped>
@import './config-styles.css';
</style>
