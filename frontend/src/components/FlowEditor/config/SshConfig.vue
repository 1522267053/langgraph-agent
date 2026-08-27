<script setup lang="ts">
import type { SshConfig } from './types'
import { fieldTypeOptions } from './types'
import { useConfigBase } from '@/composables/useConfigBase'
import { useInputVariables } from '@/composables/useInputVariables'
import VariableSelector from '../components/VariableSelector.vue'

const props = defineProps<{
  config: SshConfig
  currentNodeId: string
}>()

const emit = defineEmits<{
  (e: 'update:config', value: SshConfig): void
}>()

const { localConfig, updateConfig } = useConfigBase(() => props.config, emit)
const { addInputVariable, removeInputVariable, handleSourceTypeChange } = useInputVariables(
  localConfig,
  updateConfig
)

function updateVariableSource(index: number, source: string): void {
  if (localConfig.value.input_variables[index])
    localConfig.value.input_variables[index].source = source
  updateConfig()
}
</script>

<template>
  <div class="ssh-config">
    <div class="config-section">
      <div class="section-title">连接信息</div>
      <el-form label-width="80px" size="small">
        <el-form-item label="主机">
          <el-input
            v-model="localConfig.host"
            placeholder="远程主机 IP 或域名"
            @blur="updateConfig"
          />
        </el-form-item>
        <el-form-item label="端口">
          <el-input-number v-model="localConfig.port" :min="1" :max="65535" @change="updateConfig" />
        </el-form-item>
        <el-form-item label="用户名">
          <el-input v-model="localConfig.username" placeholder="登录用户名" @blur="updateConfig" />
        </el-form-item>
        <el-form-item label="认证方式">
          <el-radio-group v-model="localConfig.auth_type" @change="updateConfig">
            <el-radio value="password">密码</el-radio>
            <el-radio value="private_key">私钥</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="localConfig.auth_type === 'password'" label="密码">
          <el-input
            v-model="localConfig.password"
            type="password"
            show-password
            placeholder="登录密码（保存后脱敏显示）"
            @blur="updateConfig"
          />
        </el-form-item>
        <template v-else>
          <el-form-item label="私钥内容">
            <el-input
              v-model="localConfig.private_key"
              type="textarea"
              :rows="4"
              placeholder="粘贴 PEM 格式私钥内容（与私钥路径二选一，优先使用此处）"
              @blur="updateConfig"
            />
          </el-form-item>
          <el-form-item label="私钥路径">
            <el-input
              v-model="localConfig.private_key_path"
              placeholder="本机私钥文件绝对路径，未填私钥内容时使用"
              @blur="updateConfig"
            />
          </el-form-item>
          <el-form-item label="私钥口令">
            <el-input
              v-model="localConfig.passphrase"
              type="password"
              show-password
              placeholder="私钥口令，无私钥口令时留空"
              @blur="updateConfig"
            />
          </el-form-item>
        </template>
      </el-form>
      <div class="config-hint">
        <el-text size="small" type="info">
          密码/私钥保存后以 **** 脱敏显示，再次保存不会覆盖原值
        </el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">超时与传输限制</div>
      <el-form label-width="80px" size="small">
        <el-form-item label="连接超时">
          <el-input-number
            v-model="localConfig.connect_timeout"
            :min="1"
            :max="120"
            @change="updateConfig"
          />
          <span class="unit-label">秒</span>
        </el-form-item>
        <el-form-item label="命令超时">
          <el-input-number
            v-model="localConfig.command_timeout"
            :min="5"
            :max="3600"
            @change="updateConfig"
          />
          <span class="unit-label">秒</span>
        </el-form-item>
        <el-form-item label="传输上限">
          <el-input-number
            v-model="localConfig.max_transfer_mb"
            :min="1"
            :max="2048"
            @change="updateConfig"
          />
          <span class="unit-label">MB/文件</span>
        </el-form-item>
      </el-form>
    </div>

    <div class="config-section">
      <div class="section-title">
        <span>输入变量</span>
        <el-button type="primary" size="small" link @click="addInputVariable">+ 添加变量</el-button>
      </div>
      <div class="input-variables">
        <div
          v-for="(variable, index) in localConfig.input_variables"
          :key="index"
          class="input-variable"
        >
          <div class="variable-header">
            <span class="variable-index">变量 {{ index + 1 }}</span>
            <el-button type="danger" size="small" link @click="removeInputVariable(index)">
              删除
            </el-button>
          </div>
          <el-form label-width="60px" size="small">
            <el-form-item label="名称">
              <el-input v-model="variable.name" placeholder="变量名" @blur="updateConfig" />
            </el-form-item>
            <el-form-item label="类型">
              <el-select
                v-model="variable.type"
                placeholder="选择类型"
                style="width: 100%"
                @change="updateConfig"
              >
                <el-option
                  v-for="item in fieldTypeOptions"
                  :key="item.value"
                  :label="item.label"
                  :value="item.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="来源">
              <VariableSelector
                :model-value="variable.source"
                :current-node-id="currentNodeId"
                placeholder="选择变量来源"
                @update:model-value="(v: string) => updateVariableSource(index, v)"
                @update:type="t => handleSourceTypeChange(index, t)"
              />
            </el-form-item>
          </el-form>
        </div>
      </div>
      <div class="config-hint">
        <el-text size="small" type="info">
          SSH 连接直接使用上方连接信息执行，通常无需配置输入变量
        </el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">工具能力</div>
      <div class="config-hint">
        <el-text size="small" type="info">
          连接到 LLM 后，AI 可自主调用 ssh_executor（执行命令）、ssh_upload / ssh_download（SFTP
          传文件）、ssh_list_dir（目录列表）四个工具；远程高危命令请提示用户确认
        </el-text>
      </div>
    </div>

    <div class="config-section">
      <div class="section-title">输出变量</div>
      <div class="output-variables-info">
        <div v-for="ov in localConfig.output_variables" :key="ov.name" class="output-var-tag">
          <el-tag size="small" type="info">{{ ov.name }}</el-tag>
          <span class="output-var-type">{{ ov.type || '' }}</span>
        </div>
        <el-text size="small" type="info">下游节点通过变量映射使用</el-text>
      </div>
    </div>
  </div>
</template>

<style scoped>
@import './config-styles.css';

.output-variables-info {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.output-var-tag {
  display: flex;
  align-items: center;
  gap: 4px;
}
.output-var-type {
  font-size: 12px;
  color: #909399;
}
</style>
