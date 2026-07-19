"""二进制 schema 协议配置（逆向自大厅前端 JS 的 H3 常量，原样移植）。

来源：egret/js/assets-*.js，key 格式 "{protocolId}_{serviceTypeId}"，7=大厅。
"""

SCHEMA_CONFIG = {
 "10053_7": {
  "version": "26695a937138721cdec2878bf9ca16ada04535f16cd1d83d115c95548c558a38",
  "root": "Root",
  "state": 1,
  "schemas": {
   "Root": [
    {
     "name": "serverTime",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "groupId",
     "type": 1,
     "strategy": 1,
     "bit": 6
    },
    {
     "name": "gameTableMap",
     "type": 7,
     "strategy": 0,
     "keyType": 1,
     "valueSchema": "GameTable"
    }
   ],
   "GameTable": [
    {
     "name": "tableId",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "tableName",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "gameTypeName",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "physicsTableNo",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "gameCasinoName",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "gameCasinoId",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "gameStatus",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "tableOpen",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "gameTypeId",
     "type": 1,
     "strategy": 2
    },
    {
     "name": "goodRoads",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "seatLimit",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "gameFlag",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "vipBaccaratStatus",
     "type": 1,
     "strategy": 1,
     "bit": 2
    },
    {
     "name": "anchorLanguage",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "dealerLoginOut",
     "type": 1,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "tableColour",
     "type": 1,
     "strategy": 1,
     "bit": 3
    },
    {
     "name": "openStatus",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "tableMaintainStatus",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "roadPaperFlag",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "stage2BettingFlag",
     "type": 1,
     "strategy": 1,
     "bit": 2
    },
    {
     "name": "joinMode",
     "type": 1,
     "strategy": 1,
     "bit": 2
    },
    {
     "name": "drawStageStatus",
     "type": 1,
     "strategy": 1,
     "bit": 2
    },
    {
     "name": "isPausedCountDown",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "electronicDisable",
     "type": 1,
     "strategy": 1,
     "bit": 2
    },
    {
     "name": "environmentType",
     "type": 1,
     "strategy": 1,
     "bit": 2
    },
    {
     "name": "tableCurrentLanguage",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "greenScreenSign",
     "type": 1,
     "strategy": 1,
     "bit": 4,
     "defaultValue": "0"
    },
    {
     "name": "dealCardMode",
     "type": 1,
     "strategy": 1,
     "bit": 2
    },
    {
     "name": "saDisConnectFlag",
     "type": 1,
     "strategy": 1,
     "bit": 2,
     "defaultValue": "0"
    },
    {
     "name": "dealerName",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "dealerCountry",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "tableLockBetPointId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "bootNo",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "electronicCountDownEnd",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "dealerId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "dealerPic",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "dealerAccount",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "dealerEntertainPic",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "roundId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "roundNo",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "roundStatus",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "betFlag",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "countdownEndTime",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "totalBetCountDown",
     "type": 1,
     "strategy": 2
    },
    {
     "name": "videoUrl",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "tableAnchorHeadPictureUrl",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "dealerPicInstant",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "dealerPicTable",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "serverTime",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "anchorVideoHeadUrl",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "tableAnchorId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "deskVideo",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "virtualPictureUrl",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "phonePicTable",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "roadPaperVersion",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "bootReportVersion",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "betPointLimitVersion",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "miCountDown",
     "type": 1,
     "strategy": 2
    },
    {
     "name": "miCountDownEndTime",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "bootNumberLimitListVersion",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "oneselfBalanceLimit",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "shareBalanceLimit",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "activePoints",
     "type": 6,
     "strategy": 0,
     "elemType": 3
    },
    {
     "name": "tableOnline",
     "type": 5,
     "strategy": 0,
     "schema": "TableOnline"
    },
    {
     "name": "roadPaper",
     "type": 7,
     "strategy": 0,
     "keyStrategy": 2,
     "keyType": 1
    },
    {
     "name": "bootReport",
     "type": 5,
     "strategy": 0,
     "schema": "BootReport"
    },
    {
     "name": "playerSeat",
     "type": 6,
     "strategy": 0,
     "elemSchema": "PlayerSeat"
    },
    {
     "name": "seatWinStreak",
     "type": 7,
     "strategy": 0,
     "keyStrategy": 0,
     "keyType": 1,
     "valueType": 4
    },
    {
     "name": "playerTableBetLimit",
     "type": 5,
     "strategy": 0,
     "schema": "PlayerTableBetLimit"
    },
    {
     "name": "playerSideBetLimit",
     "type": 5,
     "strategy": 0,
     "schema": "PlayerTableBetLimit"
    },
    {
     "name": "goodRoadPoints",
     "type": 6,
     "strategy": 0,
     "elemSchema": "GoodRoadPoint"
    },
    {
     "name": "dealerNameLanguageMap",
     "type": 7,
     "strategy": 0,
     "keyType": 1,
     "keyStrategy": 2,
     "valueSchema": "LanguageContent"
    },
    {
     "name": "tableNameLanguageMap",
     "type": 7,
     "strategy": 0,
     "keyType": 1,
     "keyStrategy": 2,
     "valueSchema": "TableLanguageContent"
    },
    {
     "name": "anchorNameMap",
     "type": 7,
     "strategy": 0,
     "keyType": 1,
     "keyStrategy": 2,
     "valueSchema": "LanguageContent"
    },
    {
     "name": "betPointLimit",
     "type": 6,
     "strategy": 0,
     "elemSchema": "BetPointLimit"
    },
    {
     "name": "sideBetPointLimit",
     "type": 6,
     "strategy": 0,
     "elemSchema": "BetPointLimit"
    },
    {
     "name": "kenoHallStatisticCache",
     "type": 5,
     "strategy": 0,
     "schema": "KenoHallStatisticCache"
    },
    {
     "name": "sideBetGamePointDTOMap",
     "type": 7,
     "strategy": 0,
     "keyType": 1,
     "valueSchema": "SideBetGamePointDTO"
    },
    {
     "name": "currentRoundResultDTOList",
     "type": 6,
     "strategy": 0,
     "elemSchema": "CurrentRoundResultDTO"
    },
    {
     "name": "agentBootNumberLimitCache",
     "type": 5,
     "strategy": 0,
     "schema": "BootNumberLimitCache"
    }
   ],
   "TableLanguageContent": [
    {
     "name": "languageCode",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "content",
     "type": 4,
     "strategy": 0
    }
   ],
   "LanguageContent": [
    {
     "name": "languageCode",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "content",
     "type": 4,
     "strategy": 2
    }
   ],
   "TableOnline": [
    {
     "name": "onlineNumber",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "totalAmount",
     "type": 1,
     "strategy": 0
    }
   ],
   "PlayerTableBetLimit": [
    {
     "name": "min",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "max",
     "type": 1,
     "strategy": 2
    }
   ],
   "PlayerSeat": [
    {
     "name": "seatId",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "playerId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "name",
     "type": 4,
     "strategy": 0
    }
   ],
   "GoodRoadPoint": [
    {
     "name": "goodRoadType",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "goodRoadFlag",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "betPoint",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "putPoint",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "sort",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "num",
     "type": 1,
     "strategy": 0
    }
   ],
   "BootReport": [
    {
     "name": "totalCount",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "statistics",
     "type": 7,
     "strategy": 0,
     "keyStrategy": 2,
     "keyType": 1
    },
    {
     "name": "items",
     "type": 6,
     "strategy": 0,
     "elemSchema": "BootReportItem"
    }
   ],
   "BootReportItem": [
    {
     "name": "betPointId",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "betPointName",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "winCount",
     "type": 1,
     "strategy": 0
    }
   ],
   "BetPointLimit": [
    {
     "name": "betPointGroup",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "groupId",
     "type": 1,
     "strategy": 2
    },
    {
     "name": "betPointMap",
     "type": 7,
     "strategy": 0,
     "keyType": 0,
     "keyStrategy": 2,
     "valueSchema": "BetPointSimple"
    },
    {
     "name": "min",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "max",
     "type": 1,
     "strategy": 2
    },
    {
     "name": "playRate",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "dynamicFlag",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "dynamicBetPoint",
     "type": 6,
     "strategy": 0,
     "elemSchema": "DynamicBetPoint"
    },
    {
     "name": "lightPlayRate",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "lightMaxPay",
     "type": 4,
     "strategy": 2
    }
   ],
   "DynamicBetPoint": [
    {
     "name": "name",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "rate",
     "type": 4,
     "strategy": 2
    }
   ],
   "BetPointSimple": [
    {
     "name": "name",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "status",
     "type": 1,
     "strategy": 1,
     "bit": 2
    }
   ],
   "KenoHallStatisticCache": [
    {
     "name": "recentPrizes",
     "type": 6,
     "strategy": 0,
     "elemSchema": "KenoRecentPrize"
    },
    {
     "name": "roundCount",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "firstOrderCount",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "lashOrderCount",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "firstBigCount",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "tailBigCount",
     "type": 1,
     "strategy": 0
    }
   ],
   "KenoRecentPrize": [
    {
     "name": "sum",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "roundNo",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "settlementTime",
     "type": 5,
     "strategy": 0,
     "schema": "KenoSettlementTime"
    },
    {
     "name": "result",
     "type": 6,
     "strategy": 0,
     "elemType": 1
    }
   ],
   "KenoSettlementTime": [
    {
     "name": "date",
     "type": 5,
     "strategy": 0,
     "schema": "KenoDate"
    },
    {
     "name": "time",
     "type": 5,
     "strategy": 0,
     "schema": "KenoTime"
    }
   ],
   "KenoDate": [
    {
     "name": "year",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "month",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "day",
     "type": 1,
     "strategy": 0
    }
   ],
   "KenoTime": [
    {
     "name": "hour",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "minute",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "second",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "nano",
     "type": 1,
     "strategy": 0
    }
   ],
   "SideBetGamePointDTO": [
    {
     "name": "notOutNumber",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "omissionMultiple",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "bootAvgNumber",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "bootMinNumber",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "bootNumberLimit",
     "type": 1,
     "strategy": 0
    }
   ],
   "CurrentRoundResultDTO": [
    {
     "name": "cardIndex",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "cardResult",
     "type": 6,
     "strategy": 0,
     "elemSchema": "CardResultInfo"
    }
   ],
   "CardResultInfo": [
    {
     "name": "owner",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "result",
     "type": 4,
     "strategy": 0
    }
   ],
   "BootNumberLimitCache": [
    {
     "name": "gameTypeId",
     "type": 1,
     "strategy": 2
    },
    {
     "name": "list",
     "type": 6,
     "strategy": 0,
     "elemSchema": "BootNumberLimitInfo"
    },
    {
     "name": "bootNumberLimitListVersion",
     "type": 3,
     "strategy": 0
    }
   ],
   "BootNumberLimitInfo": [
    {
     "name": "betPointId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "groupId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "playRate",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "bootLimitCount",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "status",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "limitStatus",
     "type": 1,
     "strategy": 0
    }
   ]
  }
 },
 "10089_7": {
  "version": "da3d29a428cf043dbd86724edac7f25c2e9c185ca986812c01003aaf2fce8548",
  "root": "Root",
  "state": 1,
  "schemas": {
   "Root": [
    {
     "name": "size",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "hallDetails",
     "type": 6,
     "strategy": 0,
     "elemSchema": "HallDetails"
    },
    {
     "name": "hallGameTable",
     "type": 6,
     "strategy": 0,
     "elemSchema": "HallGameTable"
    }
   ],
   "HallDetails": [
    {
     "name": "gameCasinoId",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "tableNum",
     "type": 1,
     "strategy": 0
    }
   ],
   "HallGameTable": [
    {
     "name": "gameCasinoId",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "gameTypeId",
     "type": 1,
     "strategy": 2
    },
    {
     "name": "tableId",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "isTopShow",
     "type": 1,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "isAllGameTopShow",
     "type": 1,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "topSort",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "gameStatus",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "tableOpen",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "openStatus",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "index",
     "type": 1,
     "strategy": 1,
     "bit": 10
    },
    {
     "name": "groupIds",
     "type": 6,
     "strategy": 1,
     "bit": 6,
     "elemType": 1
    }
   ]
  }
 },
 "10073_7": {
  "version": "a5e098a48a2f406aaeac90ff7c7ff5f1832b098507b0e60c7cb0a014c9f5c127",
  "root": "Root",
  "state": 1,
  "schemas": {
   "Root": [
    {
     "name": "serverTime",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "playerId",
     "type": 3,
     "strategy": 0,
     "bit": 6
    },
    {
     "name": "tableBetLimitMap",
     "type": 7,
     "keyType": 2,
     "valueSchema": "TableBetLimitCache"
    }
   ],
   "TableBetLimitCache": [
    {
     "name": "tableId",
     "type": 3
    },
    {
     "name": "betPointLimit",
     "type": 6,
     "elemSchema": "GameLimitPointGroupDTO"
    },
    {
     "name": "sideBetPointLimit",
     "type": 6,
     "elemSchema": "LimitRedInfo"
    },
    {
     "name": "playerTableBetLimit",
     "type": 5,
     "schema": "LimitRedInfo"
    },
    {
     "name": "betPointLimitVersion",
     "type": 3
    }
   ],
   "GameLimitPointGroupDTO": [
    {
     "name": "betPointGroup",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "groupId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "betPointMap",
     "type": 7,
     "keyType": 2,
     "elemSchema": "BetPointSimpleDTO"
    },
    {
     "name": "min",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "max",
     "type": 1,
     "strategy": 2
    },
    {
     "name": "playRate",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "dynamicFlag",
     "type": 2,
     "strategy": 1,
     "bit": 8
    },
    {
     "name": "dynamicBetPoint",
     "type": 6,
     "elemSchema": "DynamicBetPoint"
    },
    {
     "name": "lightPlayRate",
     "type": 4
    },
    {
     "name": "lightMaxPay",
     "type": 4,
     "strategy": 0
    }
   ],
   "BetPointSimpleDTO": [
    {
     "name": "id",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "name",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "min",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "max",
     "type": 1,
     "strategy": 0
    }
   ],
   "DynamicBetPoint": [
    {
     "name": "name",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "rate",
     "type": 4,
     "strategy": 2
    }
   ],
   "LimitRedInfo": [
    {
     "name": "id",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "min",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "max",
     "type": 1,
     "strategy": 2
    }
   ],
   "BetPointLimit": [
    {
     "name": "betPointGroup",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "groupId",
     "type": 1,
     "strategy": 2
    },
    {
     "name": "betPointMap",
     "type": 7,
     "strategy": 0,
     "keyType": 0,
     "keyStrategy": 2,
     "valueSchema": "BetPointSimpleDTO"
    },
    {
     "name": "min",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "max",
     "type": 1,
     "strategy": 2
    },
    {
     "name": "playRate",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "dynamicFlag",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "dynamicBetPoint",
     "type": 6,
     "strategy": 0,
     "elemSchema": "DynamicBetPoint"
    },
    {
     "name": "lightPlayRate",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "lightMaxPay",
     "type": 4,
     "strategy": 2
    }
   ]
  }
 },
 "10075_7": {
  "version": "9c69c9b2566b7700b2aa699aa7662dbbca482eab73c3a15be047ed8e6df1e323",
  "root": "Root",
  "state": 1,
  "schemas": {
   "Root": [
    {
     "name": "versionMap",
     "type": 7,
     "strategy": None,
     "bit": None,
     "schema": None,
     "elemSchema": "versionMap",
     "elemType": None,
     "valueSchema": None,
     "valueType": None,
     "keyType": 2,
     "keyStrategy": None,
     "keyBit": None,
     "defaultValue": None
    }
   ],
   "versionMap": [
    {
     "name": "tableInfoVersion",
     "type": 3,
     "strategy": 0,
     "bit": None,
     "schema": None,
     "elemSchema": None,
     "elemType": None,
     "valueSchema": None,
     "valueType": None,
     "keyType": None,
     "keyStrategy": None,
     "keyBit": None,
     "defaultValue": None
    },
    {
     "name": "roadPaperVersion",
     "type": 3,
     "strategy": 0,
     "bit": None,
     "schema": None,
     "elemSchema": None,
     "elemType": None,
     "valueSchema": None,
     "valueType": None,
     "keyType": None,
     "keyStrategy": None,
     "keyBit": None,
     "defaultValue": None
    },
    {
     "name": "bootReportVersion",
     "type": 3,
     "strategy": 0,
     "bit": None,
     "schema": None,
     "elemSchema": None,
     "elemType": None,
     "valueSchema": None,
     "valueType": None,
     "keyType": None,
     "keyStrategy": None,
     "keyBit": None,
     "defaultValue": None
    },
    {
     "name": "betPointLimitVersion",
     "type": 3,
     "strategy": 0,
     "bit": None,
     "schema": None,
     "elemSchema": None,
     "elemType": None,
     "valueSchema": None,
     "valueType": None,
     "keyType": None,
     "keyStrategy": None,
     "keyBit": None,
     "defaultValue": None
    },
    {
     "name": "bootNumberLimitListVersion",
     "type": 3,
     "strategy": 0,
     "bit": None,
     "schema": None,
     "elemSchema": None,
     "elemType": None,
     "valueSchema": None,
     "valueType": None,
     "keyType": None,
     "keyStrategy": None,
     "keyBit": None,
     "defaultValue": None
    }
   ]
  }
 },
 "301_2": {
  "version": "0ea525bf9283b3d65a008cbb340a093d994d7c2862fdf34bebbbadfc92bcc075",
  "root": "Root",
  "state": 1,
  "schemas": {
   "Root": [
    {
     "name": "gameTableMap",
     "type": 7,
     "strategy": 0,
     "keyType": 2,
     "keyStrategy": 0,
     "valueSchema": "GameTableCacheOptDTO"
    },
    {
     "name": "bootNumberLimitMap",
     "type": 7,
     "strategy": 0,
     "keyType": 2,
     "keyStrategy": 0,
     "valueSchema": "BootNumberLimitCache"
    }
   ],
   "GameTableCacheOptDTO": [
    {
     "name": "tableId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "tableName",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "contextLanguageMap",
     "type": 7,
     "strategy": 0,
     "keyType": 1,
     "keyStrategy": 0,
     "valueSchema": "ContextLanguageRespDTO"
    },
    {
     "name": "gameStatus",
     "type": 1,
     "strategy": 1,
     "bit": 3
    },
    {
     "name": "roundStatus",
     "type": 1,
     "strategy": 1,
     "bit": 3
    },
    {
     "name": "gameTypeId",
     "type": 3,
     "strategy": 2
    },
    {
     "name": "roundId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "roundNo",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "countdownEndTime",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "totalBetCountDown",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "roadPaper",
     "type": 7,
     "strategy": 0,
     "keyStrategy": 2,
     "keyType": 1
    },
    {
     "name": "bootReport",
     "type": 5,
     "strategy": 0,
     "schema": "TableReportCache"
    },
    {
     "name": "playerBetInfoList",
     "type": 6,
     "strategy": 0,
     "elemSchema": "PlayerBetInfo"
    },
    {
     "name": "roundCards",
     "type": 6,
     "strategy": 0,
     "elemSchema": "RoundCardDTO"
    },
    {
     "name": "cardResult",
     "type": 6,
     "strategy": 0,
     "elemSchema": "CardResultInfo"
    },
    {
     "name": "currentRoundInfoStr",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "activePoints",
     "type": 6,
     "strategy": 0,
     "elemType": 3
    },
    {
     "name": "playerBetPoints",
     "type": 6,
     "strategy": 0,
     "elemType": 3
    },
    {
     "name": "winPoints",
     "type": 7,
     "strategy": 0,
     "keyType": 2,
     "keyStrategy": 0,
     "valueType": 4
    },
    {
     "name": "index",
     "type": 1,
     "strategy": 1,
     "bit": 10
    },
    {
     "name": "videoUrl",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "betPointLimit",
     "type": 6,
     "strategy": 0,
     "elemSchema": "GameLimitPointGroupInfoRespDTO"
    },
    {
     "name": "betLimit",
     "type": 5,
     "strategy": 0,
     "schema": "BetLimitInfoRespDTO"
    },
    {
     "name": "goodRoadPoints",
     "type": 6,
     "strategy": 0,
     "elemSchema": "GoodRoadPointCache"
    },
    {
     "name": "faultStatus",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "sortIndex",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "physicsTableNo",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "joinMode",
     "type": 1,
     "strategy": 1,
     "bit": 2
    }
   ],
   "BootNumberLimitCache": [
    {
     "name": "gameTypeId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "list",
     "type": 6,
     "strategy": 0,
     "elemSchema": "BetPointBootNumberLimitInfo"
    },
    {
     "name": "bootNumberLimitListVersion",
     "type": 3,
     "strategy": 0
    }
   ],
   "BetPointBootNumberLimitInfo": [
    {
     "name": "betPointId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "groupId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "betPointName",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "gameTypeId",
     "type": 3,
     "strategy": 2
    },
    {
     "name": "playRate",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "bootLimitCount",
     "type": 1,
     "strategy": 1,
     "bit": 8
    },
    {
     "name": "status",
     "type": 1,
     "strategy": 0
    }
   ],
   "PlayerBetInfo": [
    {
     "name": "playerId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "betPointId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "betAmount",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "betAt",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "betId",
     "type": 3,
     "strategy": 0
    }
   ],
   "BetLimitInfoRespDTO": [
    {
     "name": "id",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "name",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "min",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "max",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "defaultChip",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "selectedChip",
     "type": 4,
     "strategy": 2
    }
   ],
   "GameLimitPointGroupInfoRespDTO": [
    {
     "name": "betPointGroup",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "betPointIdList",
     "type": 6,
     "strategy": 0,
     "elemType": 3
    },
    {
     "name": "groupId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "betPointMap",
     "type": 7,
     "strategy": 0,
     "keyType": 2,
     "keyStrategy": 0,
     "valueSchema": "BetPointSimpleDTO"
    },
    {
     "name": "min",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "max",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "playRate",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "dynamicFlag",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "dynamicBetPoint",
     "type": 6,
     "strategy": 0,
     "elemSchema": "DynamicBetPoint"
    }
   ],
   "ContextLanguageRespDTO": [
    {
     "name": "languageCode",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "content",
     "type": 4,
     "strategy": 2
    }
   ],
   "TableReportCache": [
    {
     "name": "totalCount",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "items",
     "type": 6,
     "strategy": 0,
     "elemSchema": "TableReportDetailCache"
    }
   ],
   "TableReportDetailCache": [
    {
     "name": "betPointId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "betPointName",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "winCount",
     "type": 1,
     "strategy": 0
    }
   ],
   "BetPointSimpleDTO": [
    {
     "name": "betPointId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "betPointName",
     "type": 4,
     "strategy": 2
    }
   ],
   "DynamicBetPoint": [
    {
     "name": "betPointId",
     "type": 3,
     "strategy": 0
    }
   ],
   "RoundCardDTO": [
    {
     "name": "roundId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "roundNo",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "cardIndex",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "cardSequence",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "cardOwner",
     "type": 1,
     "strategy": 1,
     "bit": 3
    },
    {
     "name": "ownerIndex",
     "type": 1,
     "strategy": 1,
     "bit": 3
    },
    {
     "name": "cardNumber",
     "type": 1,
     "strategy": 1,
     "bit": 6
    }
   ],
   "CardResultInfo": [
    {
     "name": "owner",
     "type": 1,
     "strategy": 1,
     "bit": 3
    },
    {
     "name": "result",
     "type": 4,
     "strategy": 2
    }
   ],
   "GoodRoadPointCache": [
    {
     "name": "goodRoadType",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "goodRoadFlag",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "betPoint",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "putPoint",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "sort",
     "type": 1,
     "strategy": 1,
     "bit": 8
    },
    {
     "name": "num",
     "type": 1,
     "strategy": 1,
     "bit": 8
    }
   ]
  }
 },
 "302_2": {
  "version": "5de34be7725f7feca1bcdb09876abcaa804bc9d414837c9b7c040e9c30899927",
  "root": "Root",
  "state": 1,
  "schemas": {
   "Root": [
    {
     "name": "tableList",
     "type": 6,
     "strategy": 0,
     "elemSchema": "GoodRoadOptTableInfo"
    },
    {
     "name": "lockTableList",
     "type": 6,
     "strategy": 0,
     "elemSchema": "GoodRoadOptTableInfo"
    },
    {
     "name": "bootNumberLimitMap",
     "type": 7,
     "strategy": 0,
     "keyType": 2,
     "keyStrategy": 0,
     "valueSchema": "BootNumberLimitCache"
    }
   ],
   "GoodRoadOptTableInfo": [
    {
     "name": "tableId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "gameStatus",
     "type": 1,
     "strategy": 1,
     "bit": 3
    },
    {
     "name": "faultStatus",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "gameTypeId",
     "type": 3,
     "strategy": 2
    },
    {
     "name": "roundId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "roundNo",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "countdownEndTime",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "totalBetCountDown",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "serverTime",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "openStatus",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "roadPaper",
     "type": 7,
     "strategy": 0,
     "keyType": 1,
     "keyStrategy": 2
    },
    {
     "name": "bootReport",
     "type": 5,
     "strategy": 0,
     "schema": "TableReportCache"
    },
    {
     "name": "playerBetInfoList",
     "type": 6,
     "strategy": 0,
     "elemSchema": "PlayerBetInfo"
    },
    {
     "name": "cardResult",
     "type": 6,
     "strategy": 0,
     "elemSchema": "CardResultInfo"
    },
    {
     "name": "activePoints",
     "type": 6,
     "strategy": 0,
     "elemType": 3
    },
    {
     "name": "winPoints",
     "type": 7,
     "strategy": 0,
     "keyType": 2,
     "keyStrategy": 0
    },
    {
     "name": "betPointLimit",
     "type": 6,
     "strategy": 0,
     "elemSchema": "GameLimitPointGroupInfoRespDTO"
    },
    {
     "name": "betLimit",
     "type": 5,
     "strategy": 0,
     "schema": "BetLimitInfoRespDTO"
    },
    {
     "name": "goodRoadPoints",
     "type": 6,
     "strategy": 0,
     "elemSchema": "GoodRoadPointCache"
    },
    {
     "name": "lockGoodRoadFlag",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "location",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "contextLanguageMap",
     "type": 7,
     "strategy": 0,
     "keyType": 1,
     "keyStrategy": 0,
     "valueSchema": "ContextLanguageRespDTO"
    },
    {
     "name": "isPausedCountDown",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "isPausedCountDownTime",
     "type": 3,
     "strategy": 0
    }
   ],
   "BootNumberLimitCache": [
    {
     "name": "gameTypeId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "list",
     "type": 6,
     "strategy": 0,
     "elemSchema": "BetPointBootNumberLimitInfo"
    },
    {
     "name": "bootNumberLimitListVersion",
     "type": 3,
     "strategy": 0
    }
   ],
   "BetPointBootNumberLimitInfo": [
    {
     "name": "betPointId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "groupId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "betPointName",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "gameTypeId",
     "type": 3,
     "strategy": 2
    },
    {
     "name": "playRate",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "bootLimitCount",
     "type": 1,
     "strategy": 1,
     "bit": 8
    },
    {
     "name": "status",
     "type": 1,
     "strategy": 0
    }
   ],
   "PlayerBetInfo": [
    {
     "name": "playerId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "betPointId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "betAmount",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "betAt",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "betId",
     "type": 3,
     "strategy": 0
    }
   ],
   "BetLimitInfoRespDTO": [
    {
     "name": "id",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "name",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "min",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "max",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "defaultChip",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "selectedChip",
     "type": 4,
     "strategy": 2
    }
   ],
   "GameLimitPointGroupInfoRespDTO": [
    {
     "name": "betPointGroup",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "betPointIdList",
     "type": 6,
     "strategy": 0,
     "elemType": 3
    },
    {
     "name": "groupId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "betPointMap",
     "type": 7,
     "strategy": 0,
     "keyType": 2,
     "keyStrategy": 0,
     "valueSchema": "BetPointSimpleDTO"
    },
    {
     "name": "min",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "max",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "playRate",
     "type": 4,
     "strategy": 0
    },
    {
     "name": "dynamicFlag",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "dynamicBetPoint",
     "type": 6,
     "strategy": 0,
     "elemSchema": "DynamicBetPoint"
    }
   ],
   "ContextLanguageRespDTO": [
    {
     "name": "languageCode",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "content",
     "type": 4,
     "strategy": 2
    }
   ],
   "TableReportCache": [
    {
     "name": "totalCount",
     "type": 1,
     "strategy": 0
    },
    {
     "name": "items",
     "type": 6,
     "strategy": 0,
     "elemSchema": "TableReportDetailCache"
    }
   ],
   "TableReportDetailCache": [
    {
     "name": "betPointId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "betPointName",
     "type": 4,
     "strategy": 2
    },
    {
     "name": "winCount",
     "type": 1,
     "strategy": 0
    }
   ],
   "BetPointSimpleDTO": [
    {
     "name": "betPointId",
     "type": 3,
     "strategy": 0
    },
    {
     "name": "betPointName",
     "type": 4,
     "strategy": 2
    }
   ],
   "DynamicBetPoint": [
    {
     "name": "betPointId",
     "type": 3,
     "strategy": 0
    }
   ],
   "CardResultInfo": [
    {
     "name": "owner",
     "type": 1,
     "strategy": 1,
     "bit": 3
    },
    {
     "name": "result",
     "type": 4,
     "strategy": 2
    }
   ],
   "GoodRoadPointCache": [
    {
     "name": "goodRoadType",
     "type": 1,
     "strategy": 1,
     "bit": 4
    },
    {
     "name": "goodRoadFlag",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "betPoint",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "putPoint",
     "type": 2,
     "strategy": 1,
     "bit": 1
    },
    {
     "name": "sort",
     "type": 1,
     "strategy": 1,
     "bit": 8
    },
    {
     "name": "num",
     "type": 1,
     "strategy": 0
    }
   ]
  }
 }
}
