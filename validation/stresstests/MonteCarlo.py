from tqdm import trange
import torch
import csv
from scipy.stats import norm
import numpy as np
from validation.simulators.NerfSimulator import NerfSimulator
from validation.utils.blenderUtils import runBlenderOnFailure

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

class MonteCarlo(object):

    collisions = 0
    stepsToCollision = 0

    def __init__(self, simulator, n_simulations, steps, noise_mean, noise_std, blend_file, workspace, start_iter):
        self.simulator = simulator
        self.n_simulations = n_simulations
        self.noise_mean = noise_mean
        self.noise_std = noise_std
        self.noise_mean_cpu = noise_mean.cpu().numpy() # change this, local cpu version that plays nice with norm.pdf. Perhaps a torch native pdf would be faster
        self.noise_std_cpu = noise_std.cpu().numpy() # change this, see above
        self.steps = steps
        self.blend_file = blend_file
        self.workspace = workspace
        self.noise_seed = torch.Generator(device=device)
        self.start_iter = start_iter

    def trajectoryLikelihood(self, noise):
        # get the likelihood of a noise measurement by finding each element's probability, logging each, and returning the sum

        likelihoods = norm.pdf(noise, loc = self.noise_mean_cpu, scale = self.noise_std_cpu)
        likelihoods = np.clip(likelihoods, 1e-8, 1e8)
        logLikelihoods = np.log(likelihoods)
        return logLikelihoods.sum()

    def validate(self):
        for simulationNumber in range(self.start_iter, self.n_simulations):
            self.simulator.reset()

            outputSimulationList = []
            everCollided = False
            simTrajLogLikelihood = 0
            reward = 0
            noise_std = self.noise_std

            print(f"Starting simulation {simulationNumber}")
            for stepNumber in trange(self.steps):
                scaling_factor = 0.01 * noise_std
                scaled_reward = reward * scaling_factor
                adjusted_noise_std = noise_std + scaled_reward
                noise = torch.normal(self.noise_mean, adjusted_noise_std, generator=self.noise_seed)
                print(f"Step {stepNumber} with noise: {noise}")
                if isinstance(self.simulator, NerfSimulator):
                    isCollision, collisionVal, currentPos, sigma_d_opt, trace = self.simulator.step(noise)
                else:
                    isCollision, collisionVal, currentPos = self.simulator.step(noise)
                outputStepList = [simulationNumber, stepNumber]

                # append the noises
                noiseList = noise.cpu().numpy()

                outputStepList.extend(noiseList)
                
                # append the sdf value and positions
                outputStepList.append(collisionVal)
                outputStepList.extend(currentPos)

                # find and append the trajectory likelihood, both for this step and the entire trajectory
                curLogLikelihood = self.trajectoryLikelihood(noiseList)
                outputStepList.append(curLogLikelihood)

                simTrajLogLikelihood += curLogLikelihood
                outputStepList.append(simTrajLogLikelihood)

                if isinstance(self.simulator, NerfSimulator):
                    # calculate and handle reward/sigma
                    outputStepList.append(reward)
                    outputStepList.append(sigma_d_opt)
                    reward = self.simulator.reward(curLogLikelihood, sigma_d_opt, trace)
                
                # output the collision value
                outputStepList.append(isCollision)
                
                # append the value of the step to the simulation data
                outputSimulationList.append(outputStepList)

                if isCollision:
                    self.collisions += 1
                    self.stepsToCollision += stepNumber
                    everCollided = True
                    runBlenderOnFailure(self.blend_file, self.workspace, simulationNumber, stepNumber, outputSimulationList)
                    break
           
            '''

            Simulation data indexing:
            0: Simulation #
            1: Step #
            2-13: Noise data (12D)
            14: SDF value at position
            15-17: XYZ Coordinates
            18: step trajectory likelihood
            19: cumulative trajectory likelihood
            20: reward applied to this step
            21: Uncertainty for this step
            21: did we collide on this step
            22: did we collide on this simulation (added post facto)
            
            '''
            with open(f'./results/collisionValuesBlenderMC_n{self.n_simulations}.csv', "a") as csvFile:
                print(f"Noise List: {noiseList}")
                writer = csv.writer(csvFile)
                for outputStepList in outputSimulationList:
                    outputStepList.append(everCollided)
                    writer.writerow(outputStepList) 
 

        if self.collisions > 0:
            print(f"\n\t{self.collisions} collisions in {self.n_simulations} simulations, for a crash % of {100 * self.collisions/self.n_simulations}%\n")
            print(f"\tAverage step at collision: {self.stepsToCollision / self.collisions}\n")
