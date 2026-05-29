-- CreateTable
CREATE TABLE "HotTopic" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "platform" TEXT NOT NULL,
    "title" TEXT NOT NULL,
    "url" TEXT,
    "engagementScore" INTEGER,
    "rawData" TEXT,
    "scrapedAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "status" TEXT NOT NULL DEFAULT 'pending'
);

-- CreateTable
CREATE TABLE "Topic" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "hotTopicId" TEXT,
    "title" TEXT NOT NULL,
    "angle" TEXT,
    "format" TEXT,
    "status" TEXT NOT NULL DEFAULT 'draft',
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "Topic_hotTopicId_fkey" FOREIGN KEY ("hotTopicId") REFERENCES "HotTopic" ("id") ON DELETE SET NULL ON UPDATE CASCADE
);

-- CreateTable
CREATE TABLE "Content" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "topicId" TEXT NOT NULL,
    "diagnosisResult" TEXT,
    "draft" TEXT,
    "aiCheckResult" TEXT,
    "finalDraft" TEXT,
    "titleOptions" TEXT,
    "coverImageUrl" TEXT,
    "status" TEXT NOT NULL DEFAULT 'drafting',
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL,
    CONSTRAINT "Content_topicId_fkey" FOREIGN KEY ("topicId") REFERENCES "Topic" ("id") ON DELETE RESTRICT ON UPDATE CASCADE
);
