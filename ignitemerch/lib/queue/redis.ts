import { Redis } from "ioredis";

let redis: Redis | null = null;

export function getRedis() {
  if (redis) return redis;
  const url = process.env.REDIS_URL;
  if (!url) return null;
  redis = new Redis(url, { maxRetriesPerRequest: null, lazyConnect: true });
  return redis;
}
