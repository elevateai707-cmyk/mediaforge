import { z } from "zod";

export const creativeJobSchema = z.object({
  campaignId: z.string(),
  platform: z.enum(["TIKTOK", "YOUTUBE_SHORTS", "X", "INSTAGRAM"]),
  hookText: z.string().min(8),
  avatarSeconds: z.number().max(0.3),
});

export type CreativeJob = z.infer<typeof creativeJobSchema>;
