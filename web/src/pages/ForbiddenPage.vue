<template>
    <main class="message-page">
        <h1>无权访问</h1>
        <p>当前账号没有访问此页面的权限。</p>
        <router-link to="/projects">返回首页</router-link>
    </main>
</template>

<style scoped>
.message-page {
    max-width: 640px;
    padding: 64px 24px;
    margin: 0 auto;
    text-align: center;
}
</style>
