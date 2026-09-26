#ifndef SUPERVISOR_H
#define SUPERVISOR_H

#include <Arduino.h>
#include <FreeRTOS.h>
#include <task.h>
#include <esp_system.h>


void registerTaskWithSupervisor(TaskHandle_t handle, const char *name, uint32_t stackDepth);
void vSupervisorTask(void *pvParameters);
void vApplicationStackOverflowHook(TaskHandle_t xTask, char *pcTaskName);
void printResetReason(esp_reset_reason_t resetReason);


#endif // SUPERVISOR_H
