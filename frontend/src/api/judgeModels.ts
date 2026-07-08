import {api} from "@/api/client";

export interface Tier1Model {
  id: string;
  label: string;
  provider: string;
  supports_agentic: boolean;
  available: boolean;
}

export interface Tier1ModelList {
  default: string;
  models: Tier1Model[];
}

export const JudgeModels = {
  list(): Promise<Tier1ModelList> {
    return api.get<Tier1ModelList>("/judge-models");
  },
};
