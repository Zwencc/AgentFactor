<script setup lang="ts">
const router = useRouter()

onMounted(() => {
  router.replace('/')
})
</script>

<template>
  <div class="h-full flex-center text-muted-foreground">
    Redirecting to local dashboard...
  </div>
</template>
