<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { projectsApi, type Project } from '../../api/projects'
import { userErrorMessage, usersApi, type OwnerUser } from '../../api/users'

const users = ref<OwnerUser[]>([])
const projects = ref<Project[]>([])
const username = ref('')
const displayName = ref('')
const temporaryPassword = ref('')
const credentialDialogOpen = ref(false)
const loading = ref(false)
const saving = ref(false)
const errorMessage = ref('')

function replace(user: OwnerUser): void {
    users.value = users.value.map((current) =>
        current.id === user.id ? user : current,
    )
}

function clearTemporaryPassword(): void {
    temporaryPassword.value = ''
    credentialDialogOpen.value = false
}

function showTemporaryPassword(value: string): void {
    temporaryPassword.value = value
    credentialDialogOpen.value = true
}

function formatLastLogin(value: string | null): string {
    if (!value) return '从未登录'
    try {
        return new Intl.DateTimeFormat('zh-CN', {
            dateStyle: 'medium',
            timeStyle: 'short',
            timeZone: 'Asia/Shanghai',
        }).format(new Date(value))
    } catch {
        return '未知'
    }
}

async function load(): Promise<void> {
    loading.value = true
    errorMessage.value = ''
    try {
        const [loadedUsers, loadedProjects] = await Promise.all([
            usersApi.list(),
            projectsApi.list(),
        ])
        users.value = loadedUsers
        projects.value = loadedProjects
    } catch (error) {
        errorMessage.value = userErrorMessage(error)
    } finally {
        loading.value = false
    }
}

async function add(): Promise<void> {
    if (saving.value) return
    saving.value = true
    errorMessage.value = ''
    clearTemporaryPassword()
    try {
        const created = await usersApi.create({
            username: username.value,
            display_name: displayName.value,
            project_ids: [],
        })
        users.value.push(created.user)
        username.value = ''
        displayName.value = ''
        showTemporaryPassword(created.temporary_password)
    } catch (error) {
        errorMessage.value = userErrorMessage(error)
    } finally {
        saving.value = false
    }
}

async function resetPassword(user: OwnerUser): Promise<void> {
    if (!globalThis.confirm(`确认重置 ${user.username} 的密码吗？`)) return
    errorMessage.value = ''
    clearTemporaryPassword()
    try {
        const result = await usersApi.resetPassword(user.id)
        showTemporaryPassword(result.temporary_password)
    } catch (error) {
        errorMessage.value = userErrorMessage(error)
    }
}

async function toggle(user: OwnerUser): Promise<void> {
    const status = user.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE'
    if (
        status === 'DISABLED' &&
        !globalThis.confirm(`确认禁用 ${user.username} 吗？`)
    )
        return
    try {
        replace(await usersApi.update(user.id, { status }))
    } catch (error) {
        errorMessage.value = userErrorMessage(error)
    }
}

async function assign(user: OwnerUser, projectIds: string[]): Promise<void> {
    try {
        replace(await usersApi.replaceProjects(user.id, projectIds))
    } catch (error) {
        errorMessage.value = userErrorMessage(error)
    }
}

onMounted(load)
onBeforeUnmount(clearTemporaryPassword)
</script>

<template>
    <section class="users-page" aria-labelledby="users-title">
        <header>
            <p class="eyebrow">OWNER</p>
            <h1 id="users-title">员工账号</h1>
        </header>
        <el-card shadow="never">
            <form class="add-form" @submit.prevent="add">
                <label for="username">用户名</label>
                <el-input
                    id="username"
                    v-model="username"
                    autocomplete="off"
                    maxlength="32"
                />
                <label for="display-name">显示名称</label>
                <el-input id="display-name" v-model="displayName" />
                <el-button
                    native-type="submit"
                    type="primary"
                    :loading="saving"
                    :disabled="saving"
                    >添加员工</el-button
                >
            </form>
        </el-card>
        <el-alert
            v-if="errorMessage"
            type="error"
            :closable="false"
            show-icon
            >{{ errorMessage }}</el-alert
        >
        <div v-loading="loading" class="user-list" aria-live="polite">
            <el-card v-for="user in users" :key="user.id" shadow="never">
                <div class="user-row">
                    <div>
                        <strong>{{ user.username }}</strong>
                        <el-tag>{{ user.role }}</el-tag>
                        <p>
                            {{ user.display_name }} · {{ user.status }} ·
                            上次登录：{{ formatLastLogin(user.last_login_at) }}
                        </p>
                        <p>
                            项目：<span
                                v-for="project in user.projects"
                                :key="project.id"
                                >{{ project.name }} </span
                            ><span v-if="!user.projects.length">未分配</span>
                        </p>
                    </div>
                    <div class="user-actions">
                        <el-button
                            v-if="user.role === 'STAFF'"
                            @click="resetPassword(user)"
                            >重置密码</el-button
                        >
                        <el-button
                            :type="
                                user.status === 'ACTIVE' ? 'danger' : 'success'
                            "
                            @click="toggle(user)"
                            >{{
                                user.status === 'ACTIVE' ? '禁用' : '启用'
                            }}</el-button
                        >
                    </div>
                </div>
                <el-checkbox-group
                    v-if="user.role === 'STAFF'"
                    :model-value="user.projects.map((project) => project.id)"
                    @change="
                        (ids: unknown[]) =>
                            assign(
                                user,
                                ids.filter(
                                    (id): id is string =>
                                        typeof id === 'string',
                                ),
                            )
                    "
                >
                    <el-checkbox
                        v-for="project in projects"
                        :key="project.id"
                        :value="project.id"
                        >{{ project.name }}</el-checkbox
                    >
                </el-checkbox-group>
            </el-card>
        </div>
        <el-dialog
            v-model="credentialDialogOpen"
            title="临时密码"
            width="min(480px, 92vw)"
            :close-on-click-modal="false"
            @closed="clearTemporaryPassword"
        >
            <p>请立即安全交给员工。关闭后系统不会再次显示该密码。</p>
            <code v-if="temporaryPassword" class="temporary-password">{{
                temporaryPassword
            }}</code>
            <template #footer>
                <el-button type="primary" @click="clearTemporaryPassword"
                    >我已保存</el-button
                >
            </template>
        </el-dialog>
    </section>
</template>

<style scoped>
.users-page,
.user-list {
    display: grid;
    gap: 18px;
}
.eyebrow,
.user-row p {
    margin: 0;
    color: #909399;
}
.users-page h1 {
    margin: 6px 0 0;
}
.add-form {
    display: grid;
    grid-template-columns: 1fr 1fr auto;
    gap: 10px;
    align-items: end;
}
.user-row,
.user-actions {
    display: flex;
    gap: 16px;
}
.user-row {
    justify-content: space-between;
    margin-bottom: 12px;
}
.temporary-password {
    display: block;
    overflow-wrap: anywhere;
    padding: 14px;
    border-radius: 8px;
    background: #f5f7fa;
    font-size: 1rem;
}
@media (max-width: 680px) {
    .add-form {
        grid-template-columns: 1fr;
    }
    .user-row {
        flex-direction: column;
    }
}
</style>
