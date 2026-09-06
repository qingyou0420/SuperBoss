<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { dateTimeShort } from '../../api/parse'
import { userErrorMessage, usersApi, type OwnerUser } from '../../api/users'
import InlineError from '../../components/ui/InlineError.vue'
import PageHeader from '../../components/ui/PageHeader.vue'
import { ACCOUNT_STATUS_LABEL, ROLE_LABEL } from '../../copy/glossary'
import { membersCopy } from '../../copy/pages/members'

const users = ref<OwnerUser[]>([])
const username = ref('')
const displayName = ref('')
const createRole = ref<'STAFF' | 'MANAGER'>('STAFF')
const temporaryPassword = ref('')
const credentialDialogOpen = ref(false)
const drawerOpen = ref(false)
const loading = ref(false)
const saving = ref(false)
const errorMessage = ref('')
const pendingDisable = ref<OwnerUser>()
const disableOpen = computed({
    get: () => pendingDisable.value !== undefined,
    set: (open: boolean) => {
        if (!open) pendingDisable.value = undefined
    },
})

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
    if (!value) return membersCopy.never
    return dateTimeShort(value)
}

async function load(): Promise<void> {
    loading.value = true
    errorMessage.value = ''
    try {
        users.value = await usersApi.list()
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
            role: createRole.value,
        })
        users.value.push(created.user)
        username.value = ''
        displayName.value = ''
        createRole.value = 'STAFF'
        drawerOpen.value = false
        showTemporaryPassword(created.temporary_password)
    } catch (error) {
        errorMessage.value = userErrorMessage(error)
    } finally {
        saving.value = false
    }
}

async function resetPassword(user: OwnerUser): Promise<void> {
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
    try {
        replace(await usersApi.update(user.id, { status }))
        pendingDisable.value = undefined
    } catch (error) {
        errorMessage.value = userErrorMessage(error)
    }
}

function requestDisable(user: OwnerUser): void {
    pendingDisable.value = user
}

async function setRole(
    user: OwnerUser,
    role: 'MANAGER' | 'STAFF',
): Promise<void> {
    if (user.role === role) return
    try {
        replace(await usersApi.update(user.id, { role }))
    } catch (error) {
        errorMessage.value = userErrorMessage(error)
    }
}

async function copyPassword(): Promise<void> {
    if (!temporaryPassword.value) return
    try {
        await navigator.clipboard.writeText(temporaryPassword.value)
    } catch {
        errorMessage.value = userErrorMessage(new Error('copy'))
    }
}

onMounted(load)
onBeforeUnmount(clearTemporaryPassword)
</script>

<template>
    <section class="users-page" aria-labelledby="users-title">
        <PageHeader :title="membersCopy.title" heading-id="users-title">
            <el-button text @click="drawerOpen = true">{{
                membersCopy.add
            }}</el-button>
        </PageHeader>
        <InlineError :message="errorMessage" />
        <el-table
            v-loading="loading"
            :data="users"
            class="plain-table"
            :empty-text="membersCopy.empty"
        >
            <el-table-column :label="membersCopy.name" min-width="120">
                <template #default="{ row }">{{ row.display_name }}</template>
            </el-table-column>
            <el-table-column :label="membersCopy.username" min-width="140">
                <template #default="{ row }">{{ row.username }}</template>
            </el-table-column>
            <el-table-column :label="membersCopy.role" width="100">
                <template #default="{ row }">{{
                    ROLE_LABEL[row.role as keyof typeof ROLE_LABEL]
                }}</template>
            </el-table-column>
            <el-table-column :label="membersCopy.disabled" width="90">
                <template #default="{ row }">
                    <span v-if="row.status === 'DISABLED'">{{
                        ACCOUNT_STATUS_LABEL.DISABLED
                    }}</span>
                </template>
            </el-table-column>
            <el-table-column :label="membersCopy.lastLogin" width="140">
                <template #default="{ row }">{{
                    formatLastLogin(row.last_login_at)
                }}</template>
            </el-table-column>
            <el-table-column width="280">
                <template #default="{ row }">
                    <el-dropdown v-if="row.role !== 'OWNER'" trigger="click">
                        <el-button text>···</el-button>
                        <template #dropdown>
                            <el-dropdown-menu>
                                <el-dropdown-item
                                    v-if="row.role === 'STAFF'"
                                    @click="setRole(row, 'MANAGER')"
                                    >{{
                                        membersCopy.toManager
                                    }}</el-dropdown-item
                                >
                                <el-dropdown-item
                                    v-if="row.role === 'MANAGER'"
                                    @click="setRole(row, 'STAFF')"
                                    >{{ membersCopy.toStaff }}</el-dropdown-item
                                >
                                <el-dropdown-item @click="resetPassword(row)">{{
                                    membersCopy.resetPassword
                                }}</el-dropdown-item>
                                <el-dropdown-item
                                    v-if="row.status === 'ACTIVE'"
                                    @click="requestDisable(row)"
                                    >{{ membersCopy.disable }}</el-dropdown-item
                                >
                                <el-dropdown-item v-else @click="toggle(row)">{{
                                    membersCopy.enable
                                }}</el-dropdown-item>
                            </el-dropdown-menu>
                        </template>
                    </el-dropdown>
                </template>
            </el-table-column>
        </el-table>
        <el-dialog
            v-model="disableOpen"
            :title="membersCopy.disable"
            width="360px"
            :close-on-click-modal="false"
        >
            <p>{{ membersCopy.disableConfirm }}</p>
            <template #footer>
                <el-button @click="pendingDisable = undefined">{{
                    membersCopy.close
                }}</el-button>
                <el-button
                    type="primary"
                    @click="pendingDisable && toggle(pendingDisable)"
                    >{{ membersCopy.confirm }}</el-button
                >
            </template>
        </el-dialog>
        <el-drawer v-model="drawerOpen" :title="membersCopy.add" size="400px">
            <form class="drawer-form" @submit.prevent="add">
                <label for="username">{{ membersCopy.username }}</label>
                <el-input
                    id="username"
                    v-model="username"
                    autocomplete="off"
                    maxlength="32"
                />
                <label for="display-name">{{ membersCopy.displayName }}</label>
                <el-input id="display-name" v-model="displayName" />
                <label for="create-role">{{ membersCopy.role }}</label>
                <el-select id="create-role" v-model="createRole">
                    <el-option :label="membersCopy.staff" value="STAFF" />
                    <el-option :label="membersCopy.manager" value="MANAGER" />
                </el-select>
                <el-button
                    native-type="submit"
                    type="primary"
                    :loading="saving"
                    :disabled="saving"
                    >{{ membersCopy.addAccount }}</el-button
                >
            </form>
        </el-drawer>
        <el-dialog
            v-model="credentialDialogOpen"
            :title="membersCopy.initialPassword"
            width="min(480px, 92vw)"
            :close-on-click-modal="false"
            @closed="clearTemporaryPassword"
        >
            <p class="hint">{{ membersCopy.closeHint }}</p>
            <code v-if="temporaryPassword" class="temporary-password">{{
                temporaryPassword
            }}</code>
            <template #footer>
                <el-button @click="copyPassword">{{
                    membersCopy.copy
                }}</el-button>
                <el-button type="primary" @click="clearTemporaryPassword">{{
                    membersCopy.saved
                }}</el-button>
            </template>
        </el-dialog>
    </section>
</template>

<style scoped>
.drawer-form {
    display: grid;
    gap: 12px;
}
.hint {
    color: var(--sb-ink-3);
    font-size: var(--sb-sm);
}
.temporary-password {
    display: block;
    margin-top: 12px;
    font-family: var(--sb-mono);
    font-size: 20px;
}
</style>
